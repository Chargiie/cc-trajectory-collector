#!/usr/bin/env python3
"""高德地图 MCP 客户端（SSE 传输）。

用途:在【没有把 amap MCP 工具直接暴露给模型】的环境(裸 shell / 子进程)里,
也能调用同一个高德 MCP server(https://mcp.amap.com/sse)。
若运行环境已注册 amap MCP 工具(mcp__amap__*),优先直接用那些工具,无需本脚本。

用法:
  python3 mcp_call.py <tool_name> '<json-参数>'
  python3 mcp_call.py list                      # 列出全部工具
例:
  python3 mcp_call.py maps_geo '{"address":"西湖","city":"杭州"}'
  python3 mcp_call.py maps_weather '{"city":"杭州"}'
  python3 mcp_call.py maps_around_search '{"keywords":"咖啡","location":"120.15,30.27","radius":"800"}'

KEY 解析顺序: 环境变量 AMAP_MCP_KEY -> 同目录 key.txt -> ~/.stepfun/skills/amap/key.txt
坐标统一 "经度,纬度"。失败时打印 {"error": ...} 并退出码 1。
"""
import sys, os, json, threading, queue, time, urllib.request

BASE = "https://mcp.amap.com"


def get_key():
    k = os.environ.get("AMAP_MCP_KEY", "").strip()
    if k:
        return k
    for p in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "key.txt"),
        os.path.expanduser("~/.stepfun/skills/amap/key.txt"),
    ]:
        if os.path.exists(p):
            with open(p) as f:
                v = f.readline().strip()
                if v:
                    return v
    return ""


def die(msg):
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(1)


def run(tool, args):
    key = get_key()
    if not key:
        die("缺少高德 MCP key：设 AMAP_MCP_KEY 或放 key.txt")
    sse_url = f"{BASE}/sse?key={key}"
    q = queue.Queue()

    def reader():
        try:
            req = urllib.request.Request(sse_url, headers={"Accept": "text/event-stream"})
            r = urllib.request.urlopen(req, timeout=40)
            ev = None
            for raw in r:
                line = raw.decode("utf-8", "ignore").rstrip("\n").rstrip("\r")
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    q.put((ev, line[5:].strip())); ev = None
        except Exception as e:
            q.put(("__err__", str(e)))

    threading.Thread(target=reader, daemon=True).start()
    try:
        ev, data = q.get(timeout=15)
    except queue.Empty:
        die("连接 MCP SSE 超时")
    if ev == "__err__":
        die(f"SSE 连接失败: {data}")
    post_url = BASE + data if data.startswith("/") else data

    def post(o):
        req = urllib.request.Request(post_url, data=json.dumps(o).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()

    try:
        post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "amap-mcp-skill", "version": "1.0"}}})
        post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        if tool == "list":
            post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            want = 2
        else:
            post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": tool, "arguments": args}})
            want = 2
    except Exception as e:
        die(f"请求失败: {e}")

    end = time.time() + 20
    while time.time() < end:
        try:
            ev, data = q.get(timeout=3)
        except queue.Empty:
            continue
        if ev == "__err__":
            die(f"SSE 读取失败: {data}")
        try:
            o = json.loads(data)
        except Exception:
            continue
        if o.get("id") == want:
            if "error" in o:
                die(f"MCP 错误: {json.dumps(o['error'], ensure_ascii=False)}")
            res = o.get("result", {})
            if tool == "list":
                for t in res.get("tools", []):
                    req = t.get("inputSchema", {}).get("required") or []
                    print(f"{t['name']}\t必填:{req}")
            else:
                # tools/call: result.content 是 [{type:text,text:...}]
                parts = res.get("content", [])
                texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
                print("\n".join(texts) if texts else json.dumps(res, ensure_ascii=False))
            return
    die("等待 MCP 响应超时")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        die("用法: mcp_call.py <tool_name> '<json-参数>'  |  mcp_call.py list")
    tool = sys.argv[1]
    args = {}
    if len(sys.argv) >= 3 and sys.argv[2].strip():
        try:
            args = json.loads(sys.argv[2])
        except Exception as e:
            die(f"参数不是合法 JSON: {e}")
    run(tool, args)
