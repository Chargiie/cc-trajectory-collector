#!/usr/bin/env python3
"""Openverse image/audio search CLI. No API key required."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://api.openverse.org/v1"
USER_AGENT = "openverse-search-skill/1.0"


def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc.reason}") from exc


def compact_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "creator": item.get("creator"),
        "source": item.get("source"),
        "license": item.get("license"),
        "license_version": item.get("license_version"),
        "license_url": item.get("license_url"),
        "url": item.get("url"),
        "thumbnail": item.get("thumbnail"),
        "width": item.get("width"),
        "height": item.get("height"),
        "foreign_landing_url": item.get("foreign_landing_url"),
        "attribution": item.get("attribution"),
        "detail_url": item.get("detail_url"),
    }


def cmd_search(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {
        "q": args.query,
        "page": args.page,
        "page_size": args.page_size,
    }
    if args.license:
        params["license"] = args.license
    if args.license_type:
        params["license_type"] = args.license_type
    if args.source:
        params["source"] = args.source
    if args.excluded_source:
        params["excluded_source"] = args.excluded_source
    if args.category:
        params["category"] = args.category
    if args.aspect_ratio:
        params["aspect_ratio"] = args.aspect_ratio
    if args.size:
        params["size"] = args.size
    if args.extension:
        params["extension"] = args.extension
    if args.mature:
        params["mature"] = "true"

    media = "audio" if args.audio else "images"
    data = api_get(f"/{media}/", params)
    results = [compact_result(item) for item in data.get("results", [])]

    output = {
        "query": args.query,
        "media": media,
        "result_count": data.get("result_count"),
        "page": data.get("page"),
        "page_count": data.get("page_count"),
        "page_size": data.get("page_size"),
        "results": results,
    }

    if args.format == "table":
        print_table(output)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def print_table(data: dict[str, Any]) -> None:
    print(
        f"query={data['query']}  total={data['result_count']}  "
        f"page={data['page']}/{data['page_count']}"
    )
    for i, item in enumerate(data["results"], 1):
        title = (item.get("title") or "")[:50]
        lic = f"{item.get('license') or ''} {item.get('license_version') or ''}".strip()
        wh = ""
        if item.get("width") and item.get("height"):
            wh = f" {item['width']}x{item['height']}"
        print(f"\n[{i}] {title}")
        print(f"    source={item.get('source')}  license={lic}{wh}")
        print(f"    url={item.get('url')}")
        print(f"    landing={item.get('foreign_landing_url')}")
        if item.get("attribution"):
            print(f"    attribution={item['attribution']}")


def cmd_detail(args: argparse.Namespace) -> int:
    media = "audio" if args.audio else "images"
    data = api_get(f"/{media}/{args.id}/")
    print(json.dumps(compact_result(data), ensure_ascii=False, indent=2))
    return 0


def cmd_related(args: argparse.Namespace) -> int:
    media = "audio" if args.audio else "images"
    data = api_get(f"/{media}/{args.id}/related/", {"page_size": args.page_size})
    results = [compact_result(item) for item in data.get("results", [])]
    print(
        json.dumps(
            {
                "id": args.id,
                "result_count": data.get("result_count"),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    media = "audio" if args.audio else "images"
    data = api_get(f"/{media}/stats/")
    sources = data if isinstance(data, list) else data.get("sources", data)
    if args.format == "names":
        for src in sources:
            print(src.get("source_name"))
    else:
        print(json.dumps(sources, ensure_ascii=False, indent=2))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(args.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out.write_bytes(resp.read())
    except urllib.error.URLError as exc:
        raise SystemExit(f"Download failed: {exc.reason}") from exc
    print(str(out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search openly-licensed images and audio via Openverse API."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="Search images or audio")
    p_search.add_argument("query", help="Search query (supports Chinese)")
    p_search.add_argument("--page", type=int, default=1)
    p_search.add_argument("--page-size", type=int, default=12)
    p_search.add_argument("--license", help="Comma-separated: by,by-sa,cc0,pdm,...")
    p_search.add_argument(
        "--license-type",
        choices=["all", "all-cc", "commercial", "modification"],
        help="Shortcut license filter",
    )
    p_search.add_argument("--source", help="Comma-separated sources, e.g. wikimedia,flickr")
    p_search.add_argument("--excluded-source")
    p_search.add_argument("--category", help="photograph,digitized_artwork,illustration")
    p_search.add_argument("--aspect-ratio", choices=["tall", "wide", "square"])
    p_search.add_argument("--size", choices=["small", "medium", "large"])
    p_search.add_argument("--extension", help="Comma-separated: jpg,png,svg,...")
    p_search.add_argument("--mature", action="store_true")
    p_search.add_argument("--audio", action="store_true", help="Search audio instead of images")
    p_search.add_argument("--format", choices=["json", "table"], default="table")
    p_search.set_defaults(func=cmd_search)

    p_detail = sub.add_parser("detail", help="Get one media item by UUID")
    p_detail.add_argument("id")
    p_detail.add_argument("--audio", action="store_true")
    p_detail.set_defaults(func=cmd_detail)

    p_related = sub.add_parser("related", help="Find related media by UUID")
    p_related.add_argument("id")
    p_related.add_argument("--page-size", type=int, default=12)
    p_related.add_argument("--audio", action="store_true")
    p_related.set_defaults(func=cmd_related)

    p_stats = sub.add_parser("stats", help="List indexed sources")
    p_stats.add_argument("--audio", action="store_true")
    p_stats.add_argument("--format", choices=["json", "names"], default="names")
    p_stats.set_defaults(func=cmd_stats)

    p_download = sub.add_parser("download", help="Download a media file by direct URL")
    p_download.add_argument("url")
    p_download.add_argument("-o", "--output", required=True)
    p_download.set_defaults(func=cmd_download)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
