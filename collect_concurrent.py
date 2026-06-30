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
  python3 collect_concurrent.py --queries-file queries.txt --concurrency 4   # 探测通过才生效

并发数:--concurrency/-j 控制(默认 2);只有机器探测通过(>=8核且>=16GB)才允许加到 4,
硬上限 4。给多少 query 都行,信号量保证同时只跑 effective 个,其余排队。
"""
import argparse, os, sys, json, time, socket, subprocess, shutil, signal, datetime, glob, threading
import zipfile, csv
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import config
from lib import build_trajectory, to_sft, render_html, pty_driver
from collect import slugify, free_port, sc_baseurl_get, sc_baseurl_set, preflight

CONCURRENCY_HARD_MAX = 4   # 任何情况下并发不超过 4
_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _read_xlsx_rows(path):
    """纯标准库读 .xlsx 第一个 sheet，返回 list[dict]（表头小写做 key）。不引 openpyxl。"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_XL_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_XL_NS}t")))
        # 找第一个 worksheet
        sheet = next((n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")), None)
        if not sheet:
            return []
        root = ET.fromstring(z.read(sheet))
        grid = []
        for row in root.iter(f"{_XL_NS}row"):
            cells = {}
            for c in row.findall(f"{_XL_NS}c"):
                ref = c.get("r", "")
                col = "".join(ch for ch in ref if ch.isalpha())
                t = c.get("t")
                if t == "inlineStr":
                    is_ = c.find(f"{_XL_NS}is")
                    val = "".join(x.text or "" for x in is_.iter(f"{_XL_NS}t")) if is_ is not None else ""
                else:
                    v = c.find(f"{_XL_NS}v")
                    val = "" if v is None else (shared[int(v.text)] if t == "s" else v.text)
                cells[col] = (val or "").strip()
            grid.append(cells)
        if not grid:
            return []
        header_cells = grid[0]
        cols = sorted(header_cells.keys())          # 列字母排序
        header = {col: header_cells[col].strip().lower() for col in cols}
        rows = []
        for cells in grid[1:]:
            if not any(cells.get(col) for col in cols):
                continue                            # 跳过全空行
            rows.append({header.get(col, col): cells.get(col, "") for col in cols})
        return rows


def _rows_to_pairs(rows):
    """list[dict]（key 已小写）→ [(query_id 或 None, query)]，要求有 query 列。"""
    pairs = []
    for r in rows:
        q = (r.get("query") or "").strip()
        if not q:
            continue
        qid = (r.get("query_id") or r.get("queryid") or r.get("id") or "").strip() or None
        pairs.append((qid, q))
    return pairs


def read_queries(path):
    """返回 [(query_id 或 None, query)]。
    .xlsx → 按表头 query_id/query 取；.csv/.tsv → 同（首行表头）；其它 → 每行一个 query（无 id）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return _rows_to_pairs(_read_xlsx_rows(path))
    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        with open(path, newline="", encoding="utf-8-sig") as f:
            rd = csv.DictReader(f, delimiter=delim)
            return _rows_to_pairs([{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in rd])
    # 纯文本：每行一个 query，无 id
    return [(None, l.strip()) for l in open(path, encoding="utf-8") if l.strip()]


def _total_ram_gb():
    """总物理内存 GB，纯标准库；macOS 用 sysctl 兜底。取不到返回 0。"""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError, AttributeError):
        pass
    try:
        return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) / 1e9
    except Exception:
        return 0.0


def probe_capacity():
    """探测机器是否扛得住高并发：CPU 核数 + 总内存。
    每个并发 = 一个 sc claude(+可能 Chromium 渲染),吃 CPU 和内存。
    探测通过(>=8 核且 >=16GB)才允许加到 4，否则封顶 2。
    返回 (allowed_max, cpu, ram_gb, passed)。"""
    cpu = os.cpu_count() or 1
    ram = _total_ram_gb()
    passed = cpu >= 8 and ram >= 16
    return (CONCURRENCY_HARD_MAX if passed else 2), cpu, ram, passed


def run_one(qid, query, run_tag, port, log_dir, args, results, sem):
    with sem:   # 信号量限流：同时只放 effective 个 run 进来
        # 产物目录：有 query_id 用 id 命名（可按 id 回溯），否则用 query 关键词切片
        slug = slugify(qid) if qid else slugify(query)
        run_dir = os.path.join(config.RUNS_DIR, f"{run_tag}_{slug}")
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
    ap.add_argument("--concurrency", "-j", type=int, default=2,
                    help="并发数。默认 2；只有机器探测通过(>=8核且>=16GB)才允许加到 4，硬上限 4。")
    args = ap.parse_args()

    pairs = [(None, q) for q in args.queries]      # 命令行位置参数：无 query_id
    if args.queries_file:
        pairs += read_queries(args.queries_file)    # .xlsx/.csv/.tsv 带 query_id；.txt 无
    if not pairs:
        ap.error("至少给一个 query(位置参数或 --queries-file)")
    # 并发各 query 必须互不相同（代理按 query 内容认领 session 文件）
    qtexts = [q for _, q in pairs]
    if len(set(qtexts)) != len(qtexts):
        print("⚠ 检测到重复 query —— 并发下重复 query 无法区分 session，可能串台/认领失败，请去重。")

    # 并发数：请求值先夹到 [1, 硬上限]，再被机器探测的上限压一道（探测不过最多 2）
    allowed_max, cpu, ram, passed = probe_capacity()
    requested = max(1, min(args.concurrency, CONCURRENCY_HARD_MAX))
    effective = min(requested, allowed_max)
    print(f"[并发] 请求 {args.concurrency}→夹紧 {requested} | 探测 cpu={cpu} ram={ram:.0f}GB "
          f"→{'通过,上限4' if passed else '未过,封顶2'} | 实际并发 = {effective}")
    if requested > allowed_max:
        print(f"[并发] ⚠ 机器探测未通过，{requested} 降到 {effective}（要 4 需 >=8 核且 >=16GB）")
    sem = threading.Semaphore(effective)

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"=== 并发采集 {len(pairs)} 条 ===")

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
        for i, (qid, q) in enumerate(pairs):
            run_tag = f"{ts}-r{i}"           # 全连字符,唯一,survive memory slug
            t = threading.Thread(target=run_one, args=(qid, q, run_tag, port, log_dir, args, results, sem))
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
