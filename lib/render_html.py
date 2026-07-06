#!/usr/bin/env python3
"""trajectory.json -> 自包含 HTML 可视化(聊天式,折叠长内容)。"""
import json, sys, os, html


def render(traj):
    meta = traj.get('meta', {})

    def esc(s):
        return html.escape(str(s))

    def code(s, cls='code'):
        return f'<pre class="{cls}">{esc(s)}</pre>'

    def blocks_html(blocks):
        out = []
        for b in blocks:
            if isinstance(b, str):
                out.append(f'<div class="text">{esc(b)}</div>'); continue
            bt = b.get('type')
            if bt == 'text':
                out.append(f'<div class="text">{esc(b.get("text",""))}</div>')
            elif bt == 'thinking':
                th = b.get('thinking', '') or ''
                tag = ' · redacted(无明文)' if b.get('redacted') else ''
                inner = esc(th) if th else '<span class="muted">（仅加密签名，无明文）</span>'
                out.append(f'<details class="thinking" open><summary>thinking · {len(th)} 字符{tag}</summary>'
                           f'<div class="think-body">{inner}</div></details>')
            elif bt == 'tool_use':
                inp = json.dumps(b.get('input'), ensure_ascii=False, indent=2)
                out.append(f'<div class="tooluse"><div class="tool-head">▸ 调用 <b>{esc(b.get("name"))}</b> '
                           f'<span class="muted">{esc(b.get("id",""))}</span></div>{code(inp)}</div>')
            elif bt == 'tool_result':
                c = b.get('content', '') or ''
                err = ' tool-err' if b.get('is_error') else ''
                prev = (c[:4000] + f'\n…（共 {len(c)} 字符，已截断）') if len(c) > 4000 else c
                out.append(f'<details class="toolresult{err}"><summary>工具结果 · {len(c)} 字符'
                           f'{" · ERROR" if b.get("is_error") else ""}</summary>{code(prev)}</details>')
            else:
                out.append(code(json.dumps(b, ensure_ascii=False, indent=2)))
        return '\n'.join(out)

    cards = []
    for i, m in enumerate(traj['messages']):
        role = m.get('role'); content = m.get('content')
        if role == 'system':
            body = esc(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            cards.append(f'<details class="msg system"><summary><span class="badge sys">SYSTEM</span> '
                         f'system prompt · {len(body)} 字符（默认折叠）</summary>{code(body,"code sysprompt")}</details>')
            continue
        bl = content if isinstance(content, list) else [{'type': 'text', 'text': content}]
        cards.append(f'<div class="msg {role}"><div class="msg-head"><span class="badge {role}">{role.upper()}</span> '
                     f'<span class="muted">#{i}</span></div>{blocks_html(bl)}</div>')

    tools = traj.get('tools', [])
    tool_items = '\n'.join(
        f'<details class="tooldef"><summary><b>{esc(tt.get("name"))}</b> '
        f'<span class="muted">{esc((tt.get("description") or "")[:80])}</span></summary>'
        f'{code(json.dumps(tt.get("input_schema", tt.get("schema")), ensure_ascii=False, indent=2))}</details>'
        for tt in tools)

    return f'''<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>轨迹可视化</title>
<style>
:root{{--bg:#0f1115;--card:#1a1d24;--mut:#8b93a7;--bd:#2a2f3a;--fg:#e6e9ef;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"PingFang SC",Segoe UI,sans-serif;}}
.wrap{{max-width:920px;margin:0 auto;padding:24px 18px 80px;}}
h1{{font-size:18px;margin:0 0 4px}}
.meta{{color:var(--mut);font-size:12.5px;margin-bottom:18px;border-bottom:1px solid var(--bd);padding-bottom:14px}}
.meta b{{color:var(--fg)}}
.msg{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;margin:14px 0;}}
.msg.user{{border-left:3px solid #4a90d9}} .msg.assistant{{border-left:3px solid #4cc38a}}
.msg-head{{margin-bottom:8px}}
.badge{{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.5px;padding:2px 8px;border-radius:6px;}}
.badge.user{{background:#1c3a5e;color:#9cc7f5}} .badge.assistant{{background:#163a2b;color:#86e0b3}}
.badge.sys{{background:#3a2f16;color:#e6c178}}
.muted{{color:var(--mut);font-size:12px}}
.text{{white-space:pre-wrap;margin:6px 0}}
pre.code{{background:#0b0d11;border:1px solid var(--bd);border-radius:8px;padding:10px 12px;overflow:auto;
  font:12.5px/1.5 "SF Mono",Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word;color:#cdd6e4}}
pre.sysprompt{{max-height:420px}}
details{{margin:8px 0}} summary{{cursor:pointer;color:var(--mut);font-size:12.5px;user-select:none;padding:4px 0}}
summary:hover{{color:var(--fg)}}
.thinking{{background:#15171d;border:1px dashed #3a3f4d;border-radius:8px;padding:6px 10px}}
.thinking summary{{color:#b59cff}} .think-body{{white-space:pre-wrap;color:#c9c3e0;font-style:italic;margin-top:4px}}
.tooluse{{background:#10141a;border:1px solid #243;border-radius:8px;padding:8px 10px;margin:8px 0}}
.tool-head{{color:#86e0b3;font-size:12.5px;margin-bottom:4px}}
.toolresult{{background:#0e1116;border:1px solid var(--bd);border-radius:8px;padding:6px 10px}}
.toolresult.tool-err summary{{color:#ff8a8a}}
.tooldef{{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:6px 10px;margin:6px 0}}
.section-title{{margin:26px 0 6px;font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:1px}}
</style></head><body><div class="wrap">
<h1>轨迹可视化</h1>
<div class="meta">模型 <b>{esc(meta.get("model"))}</b> · 消息 <b>{esc(meta.get("num_messages"))}</b> 条 ·
工具 <b>{esc(meta.get("num_tools"))}</b> 个 · 主loop调用 <b>{esc(meta.get("num_main_calls"))}</b> · {esc(meta.get("captured_at"))}<br>
<span class="muted">{esc(meta.get("note",""))}</span></div>
<div class="section-title">对话</div>
{''.join(cards)}
<div class="section-title">工具定义（{len(tools)} 个，点击展开）</div>
{tool_items}
</div></body></html>'''


if __name__ == '__main__':
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(src), 'trajectory.html')
    traj = json.load(open(src))
    open(out, 'w').write(render(traj))
    print('wrote', out)
