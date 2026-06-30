#!/usr/bin/env python3
"""并发采集:N 个 query 同时跑(单机,无 Docker)。

为什么这样设计:`sc system baseurl` 是全局唯一值,无法按进程设;macOS 上 env/假HOME/配置目录
变量都改不动它。所以**不是每 run 一个端口**,而是:
  - 一个共享代理(LOG_DIR 模式:按请求头 x-claude-code-session-id 把记录分到各 session 文件),
  - 全局 baseurl 只设一次(用完还原),
  - N 个 sc claude 并发跑,各自有独立 workspace/TMPDIR(文件/memory 隔离),
  - 每个 run 按 query 认领自己的 session 文件,事后各自重建轨迹。

用法:
  python3 collect_concurrent.py "query1" "query2" [...]
  python3 collect_concurrent.py --queries-file queries.txt
"""
import argparse, os, sys, json, time, socket, subprocess, shutil, signal, datetime, glob, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import config
from lib import build_trajectory, to_sft, render_html, pty_driver
from collect import slugify, free_port, sc_baseurl_get, sc_baseurl_set, preflight


def run_one(query, run_tag, port, log_dir, args, results):
    run_dir = os.path.join(config.RUNS_DIR, f"{run_tag}_{slugify(query)}")
    workspace = os.path.join(run_dir, "workspace")
    tmp_dir = os.path.join(run_dir, "tmp")
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    print(f"[{run_tag}] 开跑: {query[:40]}")
    res = pty_driver.run_session(
        query, workspace, None, port=port, log_dir=log_dir,
        model=args.model, max_seconds=args.max_seconds, tmpdir=tmp_dir, log_cb=None,
    )
    sf = res.get("session_file")
    calls_path = os.path.join(run_dir, "calls.jsonl")
    info = {"run_dir": run_dir, "res": res, "meta": None}
    if not (sf and os.path.exists(sf)):
        print(f"[{run_tag}] ✗ 未认领到 session 文件 ({res})")
        results[run_tag] = info
        _cleanup_mem(run_tag)
        return
    shutil.copy(sf, calls_path)
    try:
        traj = build_trajectory.build(calls_path)
        json.dump(traj, open(os.path.join(run_dir, "trajectory.json"), "w"), ensure_ascii=False, indent=2)
        sft = to_sft.to_sft(traj, session_id=os.path.basename(run_dir))
        json.dump([sft], open(os.path.join(run_dir, "trajectory_sft.json"), "w"), ensure_ascii=False, indent=2)
        if not args.no_html:
            open(os.path.join(run_dir, "trajectory.html"), "w").write(render_html.render(traj))
        info["meta"] = traj["meta"]
        print(f"[{run_tag}] ✓ {res['reason']} | 主loop {traj['meta']['num_main_calls']} | {res['elapsed']}s")
    except Exception as e:
        info["meta"] = f"build失败: {e}"
        print(f"[{run_tag}] ✗ 重建失败: {e}")
    results[run_tag] = info
    _cleanup_mem(run_tag)


def _cleanup_mem(run_tag):
    # run_tag 全是连字符(无下划线/中文),在 CC 的 projects slug 里原样保留,可唯一匹配本 run
    for md in glob.glob(os.path.join(config.HOME, ".claude", "projects", f"*{run_tag}*")):
        shutil.rmtree(md, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*")
    ap.add_argument("--queries-file", help="每行一个 query 的文件")
    ap.add_argument("--model", default=config.MODEL)
    ap.add_argument("--max-seconds", type=int, default=config.MAX_SECONDS)
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args()

    queries = list(args.queries)
    if args.queries_file:
        queries += [l.strip() for l in open(args.queries_file) if l.strip()]
    if not queries:
        ap.error("至少给一个 query(位置参数或 --queries-file)")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"=== 并发采集 {len(queries)} 条 ===")

    # 一次性清 /tmp 渲染残留(skill 已改用 $TMPDIR,这里只清历史遗留)
    for pat in ("/tmp/pg-*.png", "/tmp/vg-*.png", "/tmp/checklayout_*", "/tmp/checkedges_*"):
        for p in glob.glob(pat):
            try:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass

    port = free_port(config.PROXY_PORT)
    log_dir = os.path.join(config.RUNS_DIR, f"_sharedlog_{ts}")
    os.makedirs(log_dir, exist_ok=True)
    print(f"[proxy] 共享端口 {port},分流日志目录 {log_dir}")

    proxy_env = dict(os.environ)
    proxy_env["PROXY_PORT"] = str(port)
    proxy_env["UPSTREAM_URL"] = config.UPSTREAM_URL
    proxy_env["LOG_DIR"] = log_dir            # 分流模式
    if config.THINKING_HACK:
        proxy_env["THINKING_HACK"] = "1"
        print("[proxy] content-thinking hack 已启用")
    proxy_out = open(os.path.join(log_dir, "proxy.out"), "w")
    proxy = subprocess.Popen(["node", config.PROXY_JS], env=proxy_env,
                             stdout=proxy_out, stderr=subprocess.STDOUT)
    for _ in range(40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)

    orig_baseurl = sc_baseurl_get()
    print(f"[baseurl] {orig_baseurl} -> http://127.0.0.1:{port}(只设一次)")
    results = {}
    try:
        sc_baseurl_set(f"http://127.0.0.1:{port}")
        threads = []
        for i, q in enumerate(queries):
            run_tag = f"{ts}-r{i}"           # 全连字符,唯一,survive memory slug
            t = threading.Thread(target=run_one, args=(q, run_tag, port, log_dir, args, results))
            t.start()
            threads.append(t)
            time.sleep(2)                    # 错开启动,避开同秒抢启动框
        for t in threads:
            t.join()
    finally:
        if orig_baseurl:
            sc_baseurl_set(orig_baseurl)
            print(f"[baseurl] 已还原 -> {orig_baseurl}")
        try:
            proxy.send_signal(signal.SIGTERM); proxy.wait(timeout=5)
        except Exception:
            proxy.kill()
        proxy_out.close()

    print("\n=== 完成 ===")
    for rt in sorted(results):
        info = results[rt]
        m = info["meta"]
        if isinstance(m, dict):
            print(f"  {rt}: 主loop {m['num_main_calls']} / 消息 {m['num_messages']} / 模型 {m['model']} -> {info['run_dir']}")
        else:
            print(f"  {rt}: {m} ({info['res'].get('reason')}) -> {info['run_dir']}")


if __name__ == "__main__":
    main()
