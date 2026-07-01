#!/usr/bin/env python3
"""用 PTY 驱动交互式 sc claude(非 -p),发送 query,基于 calls.jsonl 做完成检测后退出。

为什么 PTY+交互式:headless -p 模式上游不下发明文 thinking;交互式才有。
完成检测:监控 calls.jsonl,末次调用后静默 QUIET_SECONDS 且已出现主 loop end_turn 即判定结束;
max_seconds>0 时才设硬上限,默认无限(0)不主动截断。绝不使用 --continue/--resume(避免跨 run 上下文污染)。
"""
import os, sys, pty, time, select, signal, json, fcntl, termios, struct, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def _find_session_file(log_dir, query):
    """并发模式:共享代理按 x-claude-code-session-id 分文件;本 run 不知自己 sid,
    就按 query 内容在 log_dir 里认领——并发各 run query 不同,唯一匹配。返回路径或 None。"""
    probe = query.strip()[:80]  # 用 query 前缀做指纹(够区分,且避免整串转义问题)
    for p in glob.glob(os.path.join(log_dir, '*.jsonl')):
        if os.path.basename(p) == '_no_session.jsonl':
            continue
        try:
            if probe in open(p, encoding='utf-8', errors='ignore').read(200000):
                return p
        except OSError:
            continue
    return None


def _calls_state(calls_path):
    """返回 (主loop调用数, 最后一条主loop的stop_reason, 该响应里最后一个tool_use名)。
    容忍正在写入的半行。注意:只数主 loop 调用(有 system + 工具多),后台/小辅助调用不计入,
    否则它们会扰动"静默"判断。last_tool 用于识别"卡在 AskUserQuestion 反问"。"""
    n_main = 0
    last_stop = None
    last_tool = None
    if not os.path.exists(calls_path):
        return 0, None, None
    try:
        for l in open(calls_path):
            l = l.strip()
            if not l:
                continue
            try:
                o = json.loads(l)
            except Exception:
                continue
            rb = o.get('request_body')
            if (o.get('status') == 200 and isinstance(rb, dict) and rb.get('system')
                    and len(rb.get('tools', [])) > config.MAIN_MIN_TOOLS):
                n_main += 1
                ra = o.get('response_reassembled') or {}
                last_stop = ra.get('stop_reason')
                lt = None
                for b in (ra.get('content') or []):
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        lt = b.get('name')
                last_tool = lt
    except Exception:
        pass
    return n_main, last_stop, last_tool


def run_session(query, cwd, calls_path, port=None, model=None, use_bare=False,
                quiet_seconds=None, max_seconds=None, ready_delay=None, log_cb=None,
                tmpdir=None, dismiss_delay=None, log_dir=None):
    port = port or config.PROXY_PORT
    model = model or config.MODEL
    quiet_seconds = quiet_seconds if quiet_seconds is not None else config.QUIET_SECONDS
    max_seconds = max_seconds if max_seconds is not None else config.MAX_SECONDS
    ready_delay = ready_delay if ready_delay is not None else config.READY_DELAY
    dismiss_delay = dismiss_delay if dismiss_delay is not None else config.DISMISS_DELAY
    # headless 批量兜底阈值:卡在 AskUserQuestion 反问 / SSE 截断 多久后判定放行
    ask_quiet = getattr(config, "ASK_QUIET", 90)
    trunc_quiet = getattr(config, "TRUNC_QUIET", 120)
    tool_hang_quiet = getattr(config, "TOOL_HANG_QUIET", 600)

    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["ANTHROPIC_API_URL"] = f"http://127.0.0.1:{port}"
    # 显式注入 key:--bare 模式 CC 只认 ANTHROPIC_API_KEY;两个都给以兼容普通模式。
    if config.API_KEY:
        env["ANTHROPIC_API_KEY"] = config.API_KEY
        env["ANTHROPIC_AUTH_TOKEN"] = config.API_KEY
    # 每 run 独立 TMPDIR:隔离一切尊重 TMPDIR 的临时文件(如 skill 的 tempfile.mkdtemp 渲染),
    # 避免跨 run 在全局 /tmp 撞名污染。(注意:agent 在 Bash 里写死 /tmp 的路径不受此约束)
    if tmpdir:
        os.makedirs(tmpdir, exist_ok=True)
        env["TMPDIR"] = tmpdir
        env["TMP"] = tmpdir
        env["TEMP"] = tmpdir

    argv = ["sc", "claude", "--dangerously-skip-permissions", "--model", model]
    if use_bare:
        argv.append("--bare")
    # 加载自带 plugin(travel-guide-mobile-pdf / amap-mcp / step-search 三个 skill)。
    plugin_dir = getattr(config, "PLUGIN_DIR", "")
    if plugin_dir and os.path.isdir(plugin_dir):
        argv += ["--plugin-dir", plugin_dir]

    os.makedirs(cwd, exist_ok=True)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execvpe("sc", argv, env)
        except Exception:
            os._exit(127)

    # 给 PTY 一个真实窗口尺寸:Ink(Claude Code 的 TUI 框架)在 0x0 尺寸下渲染/输入会异常。
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    except OSError:
        pass

    start = time.time()
    dismissed = False
    sent = False
    sent_at = None
    resolved = None if log_dir else calls_path   # log_dir 模式:待认领的 session 文件
    last_n, _, _ = _calls_state(resolved) if resolved else (0, None, None)
    last_growth = time.time()
    reason = "max_seconds"

    def pump():
        try:
            r, _, _ = select.select([fd], [], [], 0.5)
            if r:
                data = os.read(fd, 65536)
                if data and log_cb:
                    log_cb(data.decode("utf-8", "ignore"))
                return bool(data)
        except OSError:
            return False
        return True

    while True:
        pump()
        now = time.time()

        # 先清掉 stepcode 启动期的交互选择框(如"有新版本 Update/Skip/Later",默认高亮"Later")。
        # 不清它会一直占着 stdin,后面 query 打不进 Claude Code 的输入框(实测:输入框无回显、0 主调用)。
        # 启动框不存在时,这个回车落在 CC 空输入框上=空提交,无副作用。
        if not dismissed and now - start > dismiss_delay:
            os.write(fd, b"\r")
            dismissed = True

        # 清完启动框、等界面就绪后再发 query(ready_delay 从"清框"那一刻起算)。
        if dismissed and not sent and now - start > dismiss_delay + ready_delay:
            os.write(fd, query.encode("utf-8"))
            time.sleep(0.5)
            os.write(fd, b"\r")
            sent = True
            sent_at = now
            last_growth = now  # 重置,等首个调用

        if sent:
            # log_dir 模式:发完 query 后,按 query 认领自己的 session 文件
            if resolved is None and log_dir:
                resolved = _find_session_file(log_dir, query)
            n, last_stop, last_tool = _calls_state(resolved) if resolved else (0, None, None)
            if n > last_n:
                last_n = n
                last_growth = now
            idle = now - last_growth
            # 只在"最后一步是 end_turn(真收尾)"且静默够久才判完成。
            # 若最后一步是 tool_use(还在跑工具,如 Chromium 渲染 PDF),即使长时间无新调用也继续等,
            # 只靠 end_turn 自然收尾(max_seconds<=0 时不主动截断)。
            if last_stop == 'end_turn' and idle > quiet_seconds:
                reason = "completed"
                break
            # AskUserQuestion 反问：headless 无人应答会永久挂起。
            # 预防靠「启动时给 query 拼前置自主指令」(agent 自己假设、不反问)——见 README/命令卡。
            # 这里只作**快速兜底**:真反问了就静默 ask_quiet(默认60s)后放行,不空耗。
            # (曾试 feiteng 的「连发 Enter 自动选默认项」,但 CC v2.1.196 的 AskUserQuestion TUI 不认盲 Enter,
            #  6 Enter 提交不了、只会白耗 → 去掉,改回快速放行。)
            if last_stop == 'tool_use' and last_tool == 'AskUserQuestion' and idle > ask_quiet:
                reason = "blocked_on_ask"
                break
            # 兜底2:SSE 截断(响应无 message_stop → stop_reason=None),静默超 trunc_quiet 即判截断放行。
            if n > 0 and last_stop is None and idle > trunc_quiet:
                reason = "sse_truncated"
                break
            # 兜底3:任意工具卡死(stop=tool_use 但静默远超正常渲染耗时)→ 判 tool hang 放行。
            # 阈值取宽松(默认600s):正常 Chromium 渲染 1-2 分钟内必有新调用,不会误伤;
            # AskUserQuestion 已在上面(ask_quiet 60s)先行自动应答,到这里只剩 Bash/find 等真卡死的工具。
            if last_stop == 'tool_use' and idle > tool_hang_quiet:
                reason = "tool_hang"
                break

        # max_seconds<=0 表示无限,不主动截断;>0 时才设硬上限
        if max_seconds and max_seconds > 0 and now - start > max_seconds:
            reason = "max_seconds"
            break

    # 退出会话:杀整棵进程树(pty.fork 已 setsid,子进程是会话/组长,killpg 覆盖 sc->stepcode->claude)
    try:
        os.write(fd, b"\x03"); time.sleep(0.3)
        os.write(fd, b"/exit\r"); time.sleep(1.0)
    except OSError:
        pass
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        # 有界等待回收,最多 ~5s,绝不无限阻塞
        reaped = False
        for _ in range(50):
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    reaped = True
                    break
            except ChildProcessError:
                reaped = True
                break
            time.sleep(0.1)
        if reaped:
            break
    try:
        os.close(fd)
    except OSError:
        pass

    return {"reason": reason, "elapsed": round(time.time() - start, 1),
            "num_calls": last_n, "session_file": resolved}


if __name__ == '__main__':
    # 调试用: python3 pty_driver.py "<query>" <cwd> <calls_path>
    q, cwd, cp = sys.argv[1], sys.argv[2], sys.argv[3]
    res = run_session(q, cwd, cp, log_cb=lambda s: sys.stdout.write(s))
    print("\n[pty_driver]", res)
