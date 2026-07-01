#!/usr/bin/env python3
"""StepFun Search API CLI for agents."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Try to use requests if available, fallback to urllib
try:
    import requests
    USE_REQUESTS = True
except ImportError:
    import urllib.request
    USE_REQUESTS = False

# ============================================================
# Configuration Constants
# ============================================================

DEFAULT_BASE_URL = "https://api.stepfun.com/v1/search"
DEFAULTS = {
    "n": 10,
    "category": None,
}
VALID_CATEGORIES = {"programming", "research", "gov", "business"}

ENV_API_KEY = "STEPFUN_API_KEY"
ENV_BASE_URL = "STEPFUN_SEARCH_BASE_URL"


# ============================================================
# Configuration Loading
# ============================================================

def _resolve_api_key() -> str:
    """key 解析顺序：env STEPFUN_API_KEY → 同目录 key.txt → ~/.stepfun/skills/step-search/key.txt。
    对齐 amap mcp_call.py 的多源解析，避免非交互 shell / cron / 别的机器没 export env 时静默失败。"""
    k = (os.getenv(ENV_API_KEY) or "").strip()
    if k:
        return k
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "key.txt"),
              os.path.expanduser("~/.stepfun/skills/step-search/key.txt")):
        try:
            with open(p) as f:
                k = f.read().strip()
            if k:
                return k
        except OSError:
            pass
    return ""


def load_config() -> dict[str, Any]:
    """Load configuration. key: env STEPFUN_API_KEY → key.txt → ~/.stepfun/skills/step-search/key.txt。"""
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError("System configuration incomplete - missing STEPFUN_API_KEY "
                           "(设 env，或放 scripts/key.txt / ~/.stepfun/skills/step-search/key.txt)。")

    return {
        "base_url": os.getenv(ENV_BASE_URL, DEFAULT_BASE_URL),
        "api_key": api_key,
        "defaults": DEFAULTS.copy(),
    }


# ============================================================
# Validation
# ============================================================

def normalize_n(n_value: int | None) -> int | None:
    if n_value is None:
        return None
    if not isinstance(n_value, int):
        raise TypeError("n must be an integer")
    if not 1 <= n_value <= 20:
        raise ValueError("n must be between 1 and 20")
    return n_value


def normalize_category(category: str | None) -> str | None:
    if category in (None, "", "null"):
        return None
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
        )
    return category


# ============================================================
# Search Execution
# ============================================================

def search(
    query: str,
    n: int | None = None,
    category: str | None = None,
    raw: bool = False,
) -> dict[str, Any] | None:
    """Execute search and return results or print to stdout."""
    try:
        config = load_config()
    except RuntimeError as e:
        print(f"❌ Configuration Error:\n{e}", file=sys.stderr)
        return None

    # Apply defaults
    final_n = normalize_n(n if n is not None else config["defaults"].get("n", 10))
    final_category = normalize_category(category if category is not None else config["defaults"].get("category"))

    # Build request
    payload = {"query": query, "n": final_n}
    if final_category:
        payload["category"] = final_category

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {config['api_key']}",
    }

    # Ensure UTF-8 output on Windows
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    try:
        if USE_REQUESTS:
            response = requests.post(
                config["base_url"],
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        else:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                config["base_url"],
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

    except Exception as e:
        print(f"❌ Request failed: {e}", file=sys.stderr)
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}", file=sys.stderr)
        return None

    if raw:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    results = data.get("results") or []
    if not results:
        print("⚠️  No results found.")
        print("Raw response for debugging:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    # Pretty print results
    print(f"🔍 Search: {data.get('query', query)} ({len(results)} results)")
    print("=" * 60)

    for idx, item in enumerate(results, 1):
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get("snippet", "")
        time_str = item.get("time", "")
        content = item.get("content", "")
        position = item.get("position", idx)

        prefix = f"[{position}]"
        print(f"\n{prefix} {title}")
        if time_str:
            print(f"    🕐 {time_str}")
        if url:
            print(f"    🔗 {url}")
        if snippet:
            snippet_display = snippet[:300] + ("..." if len(snippet) > 300 else "")
            print(f"    {snippet_display}")
        elif content:
            content_display = content[:500] + ("..." if len(content) > 500 else "")
            print(f"    {content_display}")

    return data


# ============================================================
# Command Line Interface
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        prog="python3 scripts/stepsearch.py",
        description="StepFun Search API search helper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "最新AI进展"
  %(prog)s "Rust WASM" --n 20
  %(prog)s "government report" --category gov
  %(prog)s "test" --json
        """
    )

    parser.add_argument("query", help="Search query")
    parser.add_argument("--n", "-n", type=int, help="Number of results (1-20, default 10)")
    parser.add_argument("--category", choices=sorted(VALID_CATEGORIES), help="Category filter")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    try:
        result = search(
            args.query,
            n=args.n,
            category=args.category,
            raw=args.json,
        )
        sys.exit(0 if result is not None else 1)
    except ValueError as e:
        print(f"❌ Argument error: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
