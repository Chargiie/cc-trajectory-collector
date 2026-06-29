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

fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
const logStream = fs.createWriteStream(LOG_FILE, { flags: 'a' });

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
  try { logStream.write(JSON.stringify(record) + '\n'); }
  catch (e) { try { process.stderr.write('[proxy] log write failed: ' + e + '\n'); } catch {} }
}

const server = http.createServer((clientReq, clientRes) => {
  const reqChunks = [];
  clientReq.on('data', c => reqChunks.push(c));
  clientReq.on('end', () => {
    const reqBody = Buffer.concat(reqChunks);

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
