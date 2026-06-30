#!/usr/bin/env python3
"""calls.jsonl -> 完整单条轨迹(system + tools + 多轮 messages,含明文 thinking)。

逐响应拼接(compaction-proof):不依赖任何单次请求的完整 history(长 run 会触发
Claude Code 上下文压缩,history 被摘要重置,跨不过去)。改为按调用顺序取每个主 loop
调用的 response 作为一个 assistant 轮,再用各请求里出现过的 tool_result 按 id 回填。
这样无论压缩多少次都能还原完整动作序列。system/tools 只有代理能拿到(本地 transcript 缺)。
"""
import json, sys, os, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

_THINK_RE = re.compile(r"<thinking(?:\s[^>]*)?>([\s\S]*?)</thinking>", re.I)
_THINK_TAG = re.compile(r"<thinking(?:\s[^>]*)?>|</thinking>", re.I)

# content-thinking hack 注入的脚手架(须与 proxy.js 保持一致),重建时从数据里剥除,
# 避免 guidance/suffix 泄漏进最终训练样本的 system / user。
_HACK_SUFFIX = "\nNow output your ultra detailed thinking block in <thinking>...</thinking>.\nDo not end this turn with announced-but-unexecuted actions — either call the tool or declare the task complete."
_HACK_ANCHOR = "Reply MUST begin with exactly ONE <thinking>"


def _strip_hack_system(text):
    """system 末尾若被注入了 hack guidance,从锚点切掉。"""
    i = text.find(_HACK_ANCHOR)
    return text[:i].rstrip() if i != -1 else text


def _strip_hack_text(text):
    """去掉注入到 user 消息末尾的 hack suffix(含其 <thinking> 字面)。"""
    return text.replace(_HACK_SUFFIX, '')


def _split_thinking(text):
    """content-thinking hack:抽出**所有**最外层 <thinking>...</thinking> 块(深度感知)。
    模型可能在 thinking 里**引用含字面 <thinking>...</thinking> 的指令**,形成内层假标签;
    用配对计数(深度归零才闭合)而非非贪婪正则,避免被内层假闭合标签提前截断。
    返回 (所有thinking拼接, 去掉全部最外层块后的剩余文本)。无标签则 ('', 原文)。"""
    spans = []          # 最外层块 (start, end)(含标签)
    depth = 0
    start = None
    for m in _THINK_TAG.finditer(text):
        if m.group().lower().startswith('</'):
            if depth > 0:
                depth -= 1
                if depth == 0:
                    spans.append((start, m.end()))
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    if not spans:
        return '', text
    thinks, rest_parts, last = [], [], 0
    for s, e in spans:
        rest_parts.append(text[last:s])
        block = text[s:e]
        inner = re.sub(r'^<thinking(?:\s[^>]*)?>', '', block, flags=re.I)
        inner = re.sub(r'</thinking>$', '', inner, flags=re.I)
        thinks.append(inner.strip())
        last = e
    rest_parts.append(text[last:])
    return '\n\n'.join(t for t in thinks if t), ''.join(rest_parts).strip()


def _is_main(o):
    rb = o.get('request_body')
    return (o.get('status') == 200 and isinstance(rb, dict) and rb.get('system')
            and len(rb.get('tools', [])) > config.MAIN_MIN_TOOLS)


def build(calls_path):
    recs = [json.loads(l) for l in open(calls_path) if l.strip()]
    main = [o for o in recs if _is_main(o)]
    if not main:
        raise RuntimeError('calls.jsonl 里没有主 loop 调用(无 system 或工具过少)')

    # 完整收尾的主 loop 调用(有 stop_reason);截断的半截调用(stop_reason=None)跳过
    complete = [o for o in main if (o.get('response_reassembled') or {}).get('stop_reason')]
    if not complete:
        raise RuntimeError('没有完整的主 loop 响应')

    first, last = main[0], complete[-1]
    fr = first['request_body']
    sysv = fr.get('system')
    _sys_blocks = ([(b.get('text', '') if isinstance(b, dict) else str(b)) for b in sysv]
                   if isinstance(sysv, list) else [sysv or ''])
    # 修2:剥掉 stepcode 当 system block#0 发的 HTTP 计费头(x-anthropic-billing-header,纯元数据噪声,非 system prompt)
    _sys_blocks = [t for t in _sys_blocks if not t.lstrip().startswith('x-anthropic-billing-header:')]
    system_text = '\n'.join(_sys_blocks)
    system_text = _strip_hack_system(system_text)  # 剥除注入的 hack guidance
    tools = fr.get('tools', [])

    # 扫所有请求,按 tool_use_id 收集 tool_result(结果出现在产生它的那次 tool_use 之后的请求里;
    # 即使之后发生压缩,该结果也已在更早的请求中出现过,扫全量即可)
    result_map = {}
    for o in recs:
        rb = o.get('request_body')
        if not isinstance(rb, dict):
            continue
        for m in rb.get('messages', []):
            c = m.get('content')
            if not isinstance(c, list):
                continue
            for b in c:
                if isinstance(b, dict) and b.get('type') == 'tool_result':
                    tid = b.get('tool_use_id')
                    if tid and tid not in result_map:
                        result_map[tid] = _clean(b)

    # 修1:合并 messages 数组里混入的 role:system(stepcode 把 agent/skill 列表当 system 塞进对话,
    # 排在首个 assistant 之前)→ 并进打头那条 system,避免最终样本出现 system→user→system 的非法序列
    _inline_sys = []
    for m in fr.get('messages', []):
        if m.get('role') == 'assistant':
            break
        if m.get('role') == 'system':
            c = m.get('content')
            _inline_sys.append(c if isinstance(c, str)
                               else '\n'.join(b.get('text', '') if isinstance(b, dict) else str(b)
                                              for b in (c or [])))
    if _inline_sys:
        system_text = system_text.rstrip() + '\n\n' + '\n\n'.join(t for t in _inline_sys if t)

    # 起始 user 轮(原始 query + 注入上下文):取第一条主 loop 请求里、首个 assistant 之前的 user 消息
    messages = [{"role": "system", "content": system_text}]
    for m in fr.get('messages', []):
        if m.get('role') == 'assistant':
            break
        if m.get('role') == 'system':
            continue   # 已并进打头 system,不再作为独立消息
        messages.append({"role": m.get('role'), "content": _norm(m.get('content'))})

    # 按调用顺序逐响应拼接:每个完整主 loop 调用 = 一个 assistant 轮,随后补它的 tool_result
    for o in complete:
        content = o['response_reassembled']['content']
        messages.append({"role": "assistant", "content": _norm(content)})
        for b in content:
            if isinstance(b, dict) and b.get('type') == 'tool_use':
                tid = b.get('id')
                if tid in result_map:
                    messages.append({"role": "user", "content": [result_map[tid]]})

    compactions = _count_compactions(main)
    return {
        "meta": {
            "model": fr.get('model'),
            "captured_at": last.get('ts'),
            "num_messages": len(messages),
            "num_tools": len(tools),
            "num_main_calls": len(main),
            "num_complete_turns": len(complete),
            "compactions": compactions,
            "note": "逐响应拼接(跨压缩);system+tools 来自代理;thinking 为交互式明文",
        },
        "system": system_text,
        "tools": tools,
        "messages": messages,
    }


def _count_compactions(main):
    """请求 messages 轮数序列里出现骤降(回落到很小)的次数 = 压缩次数。"""
    lens = [len(o['request_body'].get('messages', [])) for o in main]
    drops = 0
    for i in range(1, len(lens)):
        if lens[i] + 4 < lens[i - 1]:   # 明显回落
            drops += 1
    return drops


def _norm(content):
    blocks = ([{"type": "text", "text": content}] if isinstance(content, str)
              else [_clean(b) for b in content if isinstance(b, dict)])
    # content-thinking hack:先剥除注入的 suffix,再把 text 里内联的 <thinking> 拆成 thinking 块
    out = []
    for b in blocks:
        if b.get('type') == 'text' and b.get('text'):
            b['text'] = _strip_hack_text(b['text'])
            if '<thinking' in b['text'].lower():
                think, rest = _split_thinking(b['text'])
                if think:
                    out.append({"type": "thinking", "thinking": think, "signature": "", "redacted": False})
                    if rest:
                        out.append({"type": "text", "text": rest})
                    continue
        out.append(b)
    return out


def _clean(b):
    t = b.get('type')
    if t == 'thinking':
        return {"type": "thinking", "thinking": b.get('thinking', ''),
                "signature": b.get('signature', ''),
                "redacted": (not b.get('thinking')) and bool(b.get('signature'))}
    if t == 'text':
        return {"type": "text", "text": b.get('text', '')}
    if t == 'tool_use':
        return {"type": "tool_use", "id": b.get('id'), "name": b.get('name'), "input": b.get('input')}
    if t == 'tool_result':
        c = b.get('content')
        if isinstance(c, list):
            c = ''.join(x.get('text', '') if isinstance(x, dict) else str(x) for x in c)
        return {"type": "tool_result", "tool_use_id": b.get('tool_use_id'), "content": c,
                "is_error": b.get('is_error', False)}
    return b


if __name__ == '__main__':
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(src), 'trajectory.json')
    traj = build(src)
    json.dump(traj, open(out, 'w'), ensure_ascii=False, indent=2)
    print('wrote', out, '| messages:', traj['meta']['num_messages'],
          '| turns:', traj['meta']['num_complete_turns'], '| compactions:', traj['meta']['compactions'])

