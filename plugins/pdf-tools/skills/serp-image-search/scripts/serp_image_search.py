#!/usr/bin/env python3
"""StepFun SERP 图搜 CLI（全网图片搜索，免 key）。

用法:
  serp_image_search.py search "<关键词>" [--min-long-edge 1200] [--top 5] [--format table|json]
  serp_image_search.py download "<url>" -o out.jpg
返回字段: source / snippet / width / height / origin_image_url / doc_url / rank。
配图流程建议: search 关键词 → 按 width/height 挑(封面挑长边≥1200) → download 到 workspace/img/。

端点: search 需要 SERP 服务地址,通过环境变量 `STEPSEARCH_SERP_URL` 提供
(如 `export STEPSEARCH_SERP_URL=https://<你的-serp-host>/v1/serp_image_search`)。
未设置时 search 干净退出(退出码 3),上层图搜链自动降级到 amap POI 图。
`download` 只需图片 URL,不依赖此环境变量。
"""
import argparse, json, sys, ssl, os, urllib.request, urllib.parse

BASE = os.getenv("STEPSEARCH_SERP_URL", "").rstrip("/")
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE


def _get(url, raw=False, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read() if raw else json.loads(r.read())


def cmd_search(a):
    if not BASE:
        sys.stderr.write("✗ 未设置 STEPSEARCH_SERP_URL —— SERP 图搜不可用,图搜链请降级到 amap POI 图。\n"
                         "  内网用法: export STEPSEARCH_SERP_URL=https://<serp-host>/v1/serp_image_search\n")
        return 3
    data = _get(BASE + "?query=" + urllib.parse.quote(a.query))
    res = data.get("search_results", []) or []
    if a.min_long_edge:
        res = [x for x in res if max(x.get("width") or 0, x.get("height") or 0) >= a.min_long_edge]
    res = sorted(res, key=lambda x: -((x.get("width") or 0) * (x.get("height") or 0)))[:a.top]
    if a.format == "json":
        print(json.dumps(res, ensure_ascii=False, indent=2)); return 0
    print(f"🔍 {a.query} ({len(res)} 结果" + (f", 长边≥{a.min_long_edge}" if a.min_long_edge else "") + ")")
    for x in res:
        print(f"  {x.get('width')}x{x.get('height')}  [{x.get('source')}]  {(x.get('snippet') or '')[:40]}")
        print(f"     {x.get('origin_image_url')}")
    return 0


def cmd_download(a):
    data = _get(a.url, raw=True)
    with open(a.output, "wb") as f:
        f.write(data)
    print(f"{a.output} ({len(data)} bytes)")
    return 0


def main():
    p = argparse.ArgumentParser(description="StepFun SERP 图搜（免 key）")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("query")
    s.add_argument("--min-long-edge", type=int, default=0, help="按长边(px)过滤，封面用 1200")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--format", choices=["table", "json"], default="table")
    s.set_defaults(func=cmd_search)
    d = sub.add_parser("download"); d.add_argument("url"); d.add_argument("-o", "--output", required=True)
    d.set_defaults(func=cmd_download)
    a = p.parse_args()
    try:
        sys.exit(a.func(a))
    except Exception as e:
        sys.stderr.write(f"serp_image_search 错误: {e}\n"); sys.exit(1)


if __name__ == "__main__":
    main()
