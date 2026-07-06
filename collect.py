#!/usr/bin/env python3
"""trajectory-collector 主编排:一个 query -> SFT 格式轨迹(+ HTML)。

用法:
  python3 collect.py "你的 query"
  python3 collect.py "..." --model claude-opus-4-8 --max-seconds 600 --no-html --bare

流程:建 run 目录 -> preflight 防污染检查 -> 起专属代理 -> 切 sc baseurl(finally 还原)
     -> PTY 跑交互式 sc claude -> 重建轨迹 -> 转 SFT -> HTML -> 清理 run 的 memory 命名空间。
"""
import argparse, os, sys, re, json, time, socket, subprocess, shutil, signal, datetime, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import config
from lib import build_trajectory, to_sft, render_html
from lib import pty_driver
from lib.validate import validate_run


def slugify(q, n=32):
    s = re.sub(r'\s+', '-', q.strip())
    s = re.sub(r'[^0-9A-Za-z一-鿿\-]', '', s)
    return s[:n] or "query"


def free_port(preferred):
    for p in [preferred] + list(range(preferred + 1, preferred + 50)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:  # 未被占用
                return p
    raise RuntimeError("找不到空闲端口")


def sc_baseurl_get():
    out = subprocess.run(["sc", "system", "baseurl"], capture_output=True, text=True).stdout
    m = re.search(r'https?://[^\s]+', out)
    return m.group(0) if m else None


def sc_baseurl_set(url):
    subprocess.run(["sc", "system", "baseurl", url], capture_output=True, text=True)


def preflight(workspace):
    warns = []
    # 祖先链 CLAUDE.md
    d = workspace
    while True:
        cm = os.path.join(d, "CLAUDE.md")
        if os.path.exists(cm):
            warns.append(f"祖先 CLAUDE.md 会被加载: {cm}")
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    gcm = os.path.join(config.HOME, ".claude", "CLAUDE.md")
    if os.path.exists(gcm):
        warns.append(f"全局 CLAUDE.md 会被加载: {gcm}（每次固定加载，非可变污染）")
    return warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--model", default=config.MODEL)
    ap.add_argument("--max-seconds", type=int, default=config.MAX_SECONDS,
                    help="单 run 硬上限秒数;0=无限(默认), 只靠 end_turn 自然收尾")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--bare", action="store_true", default=config.USE_BARE)
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(config.RUNS_DIR, f"{ts}_{slugify(args.query)}")
    workspace = os.path.join(run_dir, "workspace")
    tmp_dir = os.path.join(run_dir, "tmp")
    calls_path = os.path.join(run_dir, "calls.jsonl")
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    open(calls_path, "w").close()  # 预创建空文件

    print(f"[run] {run_dir}")
    print(f"[query] {args.query}")

    # 清掉全局 /tmp 里 PDF skill 已知的渲染残留,避免读到上一轮的旧图(agent 常写死 /tmp/pg|vg-*.png)
    swept = 0
    for pat in ("/tmp/pg-*.png", "/tmp/vg-*.png", "/tmp/checklayout_*"):
        for p in glob.glob(pat):
            try:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                swept += 1
            except OSError:
                pass
    if swept:
        print(f"[preflight] 清理 /tmp 渲染残留 {swept} 项")

    for w in preflight(workspace):
        print(f"[preflight][warn] {w}")

    port = free_port(config.PROXY_PORT)
    print(f"[proxy] 端口 {port},日志 -> {calls_path}")

    # 起专属代理(本 run 独立 LOG_FILE)
    proxy_env = dict(os.environ)
    proxy_env["PROXY_PORT"] = str(port)
    proxy_env["UPSTREAM_URL"] = config.UPSTREAM_URL
    proxy_env["LOG_FILE"] = calls_path
    if config.THINKING_HACK:
        proxy_env["THINKING_HACK"] = "1"
        print("[proxy] content-thinking hack 已启用(原始 CoT 模式)")
    proxy_out = open(os.path.join(run_dir, "proxy.out"), "w")
    proxy = subprocess.Popen(["node", config.PROXY_JS], env=proxy_env,
                             stdout=proxy_out, stderr=subprocess.STDOUT)
    # 等监听就绪
    for _ in range(40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)

    orig_baseurl = sc_baseurl_get()
    print(f"[baseurl] 原值 {orig_baseurl} -> http://127.0.0.1:{port}")
    session_result = None
    try:
        sc_baseurl_set(f"http://127.0.0.1:{port}")
        session_result = pty_driver.run_session(
            args.query, workspace, calls_path, port=port,
            model=args.model, use_bare=args.bare, max_seconds=args.max_seconds,
            tmpdir=tmp_dir,
            log_cb=lambda s: sys.stdout.write(s),
        )
    finally:
        if orig_baseurl:
            sc_baseurl_set(orig_baseurl)
            print(f"\n[baseurl] 已还原 -> {orig_baseurl}")
        try:
            proxy.send_signal(signal.SIGTERM)
            proxy.wait(timeout=5)
        except Exception:
            proxy.kill()
        proxy_out.close()

    print(f"[session] {session_result}")

    # 重建 -> SFT -> HTML
    traj = build_trajectory.build(calls_path)
    traj_path = os.path.join(run_dir, "trajectory.json")
    json.dump(traj, open(traj_path, "w"), ensure_ascii=False, indent=2)

    sft = to_sft.to_sft(traj, session_id=os.path.basename(run_dir))
    sft_path = os.path.join(run_dir, "trajectory_sft.json")
    json.dump([sft], open(sft_path, "w"), ensure_ascii=False, indent=2)

    html_path = None
    if not args.no_html:
        html_path = os.path.join(run_dir, "trajectory.html")
        open(html_path, "w").write(render_html.render(traj))

    # 收尾:删除本 run 的 memory 命名空间,避免堆积。
    # CC 的 projects slug 会把 /、_、中文、逗号等都替换成 -,精确路径难复现;
    # 改用 run 时间戳(唯一且在 slug 中原样保留)glob 匹配删除。
    for mem_dir in glob.glob(os.path.join(config.HOME, ".claude", "projects", f"*{ts}*")):
        shutil.rmtree(mem_dir, ignore_errors=True)
        print(f"[cleanup] 删除 memory 命名空间 {mem_dir}")

    print("\n=== 完成 ===")
    print(f"  轨迹(中间态): {traj_path}")
    print(f"  SFT 训练格式 : {sft_path}")
    if html_path:
        print(f"  可视化 HTML  : {html_path}")
    print(f"  消息 {traj['meta']['num_messages']} 条 / 工具 {traj['meta']['num_tools']} 个 / "
          f"主loop {traj['meta']['num_main_calls']} 次 / 模型 {traj['meta']['model']}")
    tk = traj['meta'].get('tokens', {})
    print(f"  token: 输入 {tk.get('input_tokens',0)} + 缓存写 {tk.get('cache_creation_input_tokens',0)} + "
          f"缓存读 {tk.get('cache_read_input_tokens',0)} / 输出 {tk.get('output_tokens',0)} / "
          f"总吞吐 {tk.get('total_tokens',0)}")
    # 即时校验
    warnings = validate_run(run_dir, sft_data=sft)
    if warnings:
        print(f"  ⚠ 质量问题: {' | '.join(warnings)}")
    else:
        print(f"  ✓ 校验通过(7项全绿)")


if __name__ == "__main__":
    main()
