#!/usr/bin/env node
'use strict';
// Transparent logging reverse-proxy for Claude Code.
// Claude Code -> this proxy (http://localhost:PORT) -> UPSTREAM (https) -> back.
// Forwards everything verbatim; tees full request+response into a single JSONL.
// Design rule: forwarding NEVER depends on logging. If logging throws, the call still goes through.

const http = require('http');
const https = require('https');
const zlib = require('zlib');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

const PORT = parseInt(process.env.PROXY_PORT || '8787', 10);
const UPSTREAM = new URL(process.env.UPSTREAM_URL || 'https://models-proxy.stepfun-inc.com');
const LOG_FILE = process.env.LOG_FILE || path.join(__dirname, 'logs', 'calls.jsonl');
const THINKING_HACK = process.env.THINKING_HACK === '1';

// content-thinking hack:关原生 thinking,让模型把 <thinking>...</thinking> 当普通正文输出,
// 从而拿到「原始完整 CoT」而非 Anthropic 的摘要。只改主 loop 请求(有 system+tools)。
const HACK_GUIDANCE = [
  "Reply MUST begin with exactly ONE <thinking>...</thinking> block containing ALL of your reasoning for this turn — ultra detailed: current state, evidence gathered, full plan, and the next action(s).",
  "Think once, upfront. After the closing </thinking>, do NOT write any further <thinking> blocks in the same reply.",
  "After the thinking block, respond normally as you otherwise would: your visible reply text to the user and/or native tool calls, whatever this turn needs. Do not suppress your normal user-facing response.",
  "When the task is finished and no tool is needed, the reply is: the <thinking> block, then your complete final answer to the user.",
  "Use the native tool-calling interface when a tool is needed; never fabricate tool results — wait for the next observation.",
].join('\n');
const HACK_SUFFIX = "\nNow output your ultra detailed thinking block in <thinking>...</thinking>.";

function applyThinkingHack(reqBody) {
  // 返回(可能改写后的)body buffer;任何异常都退回原 body,绝不影响转发。
  try {
    const body = JSON.parse(reqBody.toString('utf8'));
    if (!body || !Array.isArray(body.messages) || !body.system
        || !Array.isArray(body.tools) || body.tools.length <= 10) {
      return reqBody;  // 非主 loop 请求,不动
    }
    body.thinking = { type: 'disabled' };
    delete body.output_config;  // effort 与 thinking 绑定,一并去掉
    if (Array.isArray(body.system)) body.system.push({ type: 'text', text: HACK_GUIDANCE });
    else body.system = String(body.system || '') + '\n' + HACK_GUIDANCE;
    const last = body.messages[body.messages.length - 1];
    if (last && last.role === 'user') {
      if (Array.isArray(last.content)) last.content.push({ type: 'text', text: HACK_SUFFIX });
      else last.content = String(last.content || '') + HACK_SUFFIX;
    }
    return Buffer.from(JSON.stringify(body), 'utf8');
  } catch {
    return reqBody;
  }
}

fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
const logStream = fs.createWriteStream(LOG_FILE, { flags: 'a' });

// 并发分流:LOG_DIR 模式下,按请求头 x-claude-code-session-id 把每条记录写到
// LOG_DIR/<sid>.jsonl(无 sid 的后台探测进 _no_session.jsonl)。一个共享代理即可服务
// 多个并发 sc claude 会话,事后按 sid 各自重建。未设 LOG_DIR 时走原单文件 LOG_FILE。
const LOG_DIR = process.env.LOG_DIR || '';
const _dirStreams = {};
if (LOG_DIR) fs.mkdirSync(LOG_DIR, { recursive: true });
function dirStream(sid) {
  const key = (sid && /^[0-9a-fA-F-]{8,}$/.test(sid)) ? sid : '_no_session';
  if (!_dirStreams[key]) {
    _dirStreams[key] = fs.createWriteStream(path.join(LOG_DIR, key + '.jsonl'), { flags: 'a' });
  }
  return _dirStreams[key];
}

function safeJSON(buf) {
  try { return JSON.parse(buf.toString('utf8')); } catch { return null; }
}

// Best-effort reassembly of an Anthropic streaming (SSE) response into a single message.
function reassembleSSE(text) {
  try {
    const events = [];
    const normalized = text.replace(/\r\n/g, '\n');
    for (const block of normalized.split('\n\n')) {
      const dataLines = block.split('\n').filter(l => l.startsWith('data:'));
      if (!dataLines.length) continue;
      const payload = dataLines.map(l => l.slice(5).trim()).join('');
      try { events.push(JSON.parse(payload)); } catch {}
    }
    const blocks = [];
    let stopReason = null, usage = null, model = null, role = 'assistant';
    for (const e of events) {
      if (e.type === 'message_start' && e.message) {
        model = e.message.model; role = e.message.role || role;
        usage = e.message.usage || usage;
      } else if (e.type === 'content_block_start') {
        blocks[e.index] = JSON.parse(JSON.stringify(e.content_block));
        if (blocks[e.index].type === 'text' && blocks[e.index].text == null) blocks[e.index].text = '';
        if (blocks[e.index].type === 'thinking' && blocks[e.index].thinking == null) blocks[e.index].thinking = '';
        if (blocks[e.index].type === 'tool_use') blocks[e.index]._partial_json = '';
      } else if (e.type === 'content_block_delta') {
        const b = blocks[e.index]; if (!b) continue;
        const d = e.delta || {};
        if (d.type === 'text_delta') b.text = (b.text || '') + (d.text || '');
        else if (d.type === 'thinking_delta') b.thinking = (b.thinking || '') + (d.thinking || '');
        else if (d.type === 'input_json_delta') b._partial_json = (b._partial_json || '') + (d.partial_json || '');
        else if (d.type === 'signature_delta') b.signature = (b.signature || '') + (d.signature || '');
      } else if (e.type === 'message_delta') {
        if (e.delta && e.delta.stop_reason) stopReason = e.delta.stop_reason;
        if (e.usage) usage = Object.assign({}, usage, e.usage);
      }
    }
    for (const b of blocks) {
      if (b && b.type === 'tool_use' && b._partial_json != null) {
        try { b.input = JSON.parse(b._partial_json || '{}'); } catch {}
        delete b._partial_json;
      }
    }
    return { role, model, stop_reason: stopReason, usage, content: blocks.filter(Boolean), event_count: events.length };
  } catch { return null; }
}

function decodeBody(buf, encoding) {
  try {
    if (encoding === 'gzip') return zlib.gunzipSync(buf);
    if (encoding === 'br') return zlib.brotliDecompressSync(buf);
    if (encoding === 'deflate') return zlib.inflateSync(buf);
  } catch {}
  return buf;
}

function writeLog(record) {
  try {
    const line = JSON.stringify(record) + '\n';
    if (LOG_DIR) {
      const sid = (record.request_headers || {})['x-claude-code-session-id'];
      dirStream(sid).write(line);
    } else {
      logStream.write(line);
    }
  } catch (e) { try { process.stderr.write('[proxy] log write failed: ' + e + '\n'); } catch {} }
}

const server = http.createServer((clientReq, clientRes) => {
  const reqChunks = [];
  clientReq.on('data', c => reqChunks.push(c));
  clientReq.on('end', () => {
    let reqBody = Buffer.concat(reqChunks);
    if (THINKING_HACK && reqBody.length) reqBody = applyThinkingHack(reqBody);

    // Build upstream headers: copy verbatim, only fix host. Strip accept-encoding so
    // upstream returns identity -> clean SSE capture (client doesn't require gzip).
    const headers = Object.assign({}, clientReq.headers);
    headers['host'] = UPSTREAM.host;
    delete headers['accept-encoding'];
    if (reqBody.length) headers['content-length'] = String(reqBody.length);

    const opts = {
      protocol: UPSTREAM.protocol,
      hostname: UPSTREAM.hostname,
      port: UPSTREAM.port || (UPSTREAM.protocol === 'https:' ? 443 : 80),
      method: clientReq.method,
      path: clientReq.url,
      headers,
    };
    const transport = UPSTREAM.protocol === 'https:' ? https : http;
    const startedAt = new Date();

    const upstreamReq = transport.request(opts, upstreamRes => {
      clientRes.writeHead(upstreamRes.statusCode, upstreamRes.headers);
      const respChunks = [];
      upstreamRes.on('data', chunk => {
        clientRes.write(chunk);     // forward to client first (preserve streaming)
        respChunks.push(chunk);     // then tee for logging
      });
      upstreamRes.on('end', () => {
        clientRes.end();
        try {
          const rawResp = Buffer.concat(respChunks);
          const enc = (upstreamRes.headers['content-encoding'] || '').toLowerCase();
          const decoded = decodeBody(rawResp, enc);
          const ctype = (upstreamRes.headers['content-type'] || '');
          const isSSE = ctype.includes('event-stream');
          const text = decoded.toString('utf8');
          const record = {
            ts: startedAt.toISOString(),
            duration_ms: Date.now() - startedAt.getTime(),
            method: clientReq.method,
            path: clientReq.url,
            upstream: UPSTREAM.origin,
            request_headers: clientReq.headers,
            request_body: safeJSON(reqBody) || reqBody.toString('utf8'),
            status: upstreamRes.statusCode,
            response_headers: upstreamRes.headers,
            response_is_sse: isSSE,
            response_raw: isSSE ? text : (safeJSON(decoded) || text),
            response_reassembled: isSSE ? reassembleSSE(text) : null,
          };
          writeLog(record);
        } catch (e) {
          writeLog({ ts: startedAt.toISOString(), path: clientReq.url, log_error: String(e) });
        }
      });
    });

    upstreamReq.on('error', err => {
      try { if (!clientRes.headersSent) clientRes.writeHead(502, { 'content-type': 'text/plain' }); clientRes.end('proxy upstream error: ' + err.message); } catch {}
      writeLog({ ts: startedAt.toISOString(), path: clientReq.url, upstream_error: String(err) });
    });

    if (reqBody.length) upstreamReq.write(reqBody);
    upstreamReq.end();
  });
  clientReq.on('error', () => { try { clientRes.destroy(); } catch {} });
});

server.listen(PORT, '127.0.0.1', () => {
  process.stderr.write(`[proxy] listening on http://127.0.0.1:${PORT} -> ${UPSTREAM.origin}\n`);
  process.stderr.write(`[proxy] logging to ${LOG_FILE}\n`);
});
