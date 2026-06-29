#!/usr/bin/env python3
"""trajectory.json -> SFT 扁平格式(OpenAI 风格,对齐 vm-trajectory-export 样本)。

- tools: input_schema/schema -> parameters
- content 一律字符串;assistant 的 thinking -> reasoning_content,工具调用 -> tool_calls,带 loss_mask
- 工具结果 -> 独立 tool 角色(tool_call_id / name / content / is_error)
"""
import json, sys, os


def to_sft(traj, session_id="trajectory-collector", agent="claude-code"):
    tools_out = [{
        "name": tt.get("name"),
        "description": tt.get("description", ""),
        "parameters": tt.get("input_schema", tt.get("schema", {})),
    } for tt in traj.get('tools', [])]

    messages_out = []
    id2name = {}
    for m in traj.get('messages', []):
        role = m.get('role')
        content = m.get('content')
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]

        if role == 'system':
            messages_out.append({"role": "system",
                                 "content": content if isinstance(content, str) else _text(blocks)})
        elif role == 'user':
            results = [b for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_result']
            if results:
                for tr in results:
                    tcid = tr.get('tool_use_id')
                    c = tr.get('content')
                    messages_out.append({
                        "role": "tool",
                        "tool_call_id": tcid,
                        "name": id2name.get(tcid, ""),
                        "content": c if isinstance(c, str) else json.dumps(c, ensure_ascii=False),
                        "is_error": bool(tr.get('is_error', False)),
                    })
            else:
                messages_out.append({"role": "user",
                                     "content": content if isinstance(content, str) else _text(blocks)})
        elif role == 'assistant':
            tool_calls = []
            for b in blocks:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    tid = b.get('id')
                    id2name[tid] = b.get('name')
                    tool_calls.append({
                        "id": tid, "type": "function",
                        "function": {"name": b.get('name'),
                                     "arguments": json.dumps(b.get('input', {}), ensure_ascii=False)},
                    })
            msg = {"role": "assistant", "content": _text(blocks),
                   "reasoning_content": _think(blocks), "loss_mask": 1}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            messages_out.append(msg)

    return {
        "session_id": session_id,
        "agent": agent,
        "session_key": session_id,
        "tools": tools_out,
        "messages": messages_out,
    }


def _text(blocks):
    return ''.join(b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text')


def _think(blocks):
    return ''.join(b.get('thinking', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'thinking')


if __name__ == '__main__':
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(src), 'trajectory_sft.json')
    sid = sys.argv[3] if len(sys.argv) > 3 else os.path.basename(os.path.dirname(src))
    traj = json.load(open(src))
    sft = to_sft(traj, session_id=sid)
    json.dump([sft], open(out, 'w'), ensure_ascii=False, indent=2)
    print('wrote', out, '| 角色:', [m['role'] for m in sft['messages']], '| tools:', len(sft['tools']))
