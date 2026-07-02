#!/usr/bin/env python3
"""页边距 + 居中硬闸：把「留白不过量 + 未居中 + 贴边」从主观目测变成硬数字。

检测每页：
  - 页顶 / 页底留白%（基于行内自身均匀度，不依赖全局背景色）
  - 左右内容边到纸张边的距离（≥min-edge 才不贴边）
  - 居中：top - bottom 的绝对差（≤max-center-delta 才居中）
超阈 → 退出码 1（真闸，可被流程拦住）。MUST NOT 2>/dev/null。

封面（第 1 页）默认跳过（满版图）；--no-skip-cover 关闭。
设计页数（`.page` 段数）通过 --expect-pages 给，物理页数 != 此值 → FAIL（页溢出 / 塌页）。

用法：
    python3 check_edges.py out.pdf                                          # 默认阈值
    python3 check_edges.py out.pdf --expect-pages 8                         # 8 页设计
    python3 check_edges.py out.pdf --max-bottom 12 --max-center-delta 8 --min-edge 10
    python3 check_edges.py '$TMPDIR/pg-*.png'                                   # 直接喂已渲染 PNG
"""
import sys, os, glob, argparse, tempfile, subprocess, re
import numpy as np
from PIL import Image


def page_size_pt(pdf_path):
    """用 pdfinfo 读页面 pt 尺寸，返回 (w, h)；读不到返回 None。"""
    try:
        out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True).stdout
        m = re.search(r"Page size:\s*([\d.]+)\s*x\s*([\d.]+)\s*pts", out)
        return (float(m.group(1)), float(m.group(2))) if m else None
    except Exception:
        return None


def render_pdf_to_pngs(pdf_path, dpi, outdir):
    prefix = os.path.join(outdir, "pg")
    r = subprocess.run(["pdftoppm", "-r", str(dpi), "-png", pdf_path, prefix],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"pdftoppm 渲染失败 (exit {r.returncode})")
    pngs = sorted(glob.glob(prefix + "*.png"))
    if not pngs:
        raise SystemExit("pdftoppm 没产出 PNG —— 渲染异常")
    return pngs


def row_is_blank(row, tol=5, row_match=0.985):
    """行内自身均匀度判空白：≥row_match 的像素落在该行中位色 tol 邻域内。

    tol 默认 5（而非 8）——CJK 小字号文本经 anti-aliasing 后像素差值仅 5–10，
    tol=8 会把这些行误判为空白。降到 5 可捕捉 ≥10px CJK 字。
    如果产物仍误报空白（极小字号 <9px），可手动传 tol=3。
    """
    med = np.median(row, axis=0)
    diff = np.abs(row - med).max(axis=1)
    frac = float((diff <= tol).mean())
    return frac >= row_match


def edge_whitespace_pct(png_path, side='bottom', tol=8, row_match=0.985):
    """从某一侧向内数连续空白行，返回 (留白百分比, 总高px)。side: 'top' / 'bottom'。"""
    img = Image.open(png_path).convert("RGB")
    arr = np.asarray(img, dtype=np.int16)
    h, w, _ = arr.shape
    rng = range(h) if side == 'top' else range(h - 1, -1, -1)
    blank = 0
    for y in rng:
        if row_is_blank(arr[y], tol, row_match):
            blank += 1
        else:
            break
    return blank / h * 100.0, h


def content_edge_distance_px(png_path, side='left', tol=8, col_match=0.985):
    """从某一侧向内数连续空白列，返回到首个内容列的距离（px）。side: 'left' / 'right'。"""
    img = Image.open(png_path).convert("RGB")
    arr = np.asarray(img, dtype=np.int16)
    h, w, _ = arr.shape
    rng = range(w) if side == 'left' else range(w - 1, -1, -1)
    for x in rng:
        col = arr[:, x, :]
        med = np.median(col, axis=0)
        diff = np.abs(col - med).max(axis=1)
        frac = float((diff <= tol).mean())
        if frac < col_match:
            return w - x - 1 if side == 'right' else x
    return 0 if side == 'right' else w  # 全空白


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="PDF 路径，或 PNG 路径/glob（如 '$TMPDIR/pg-*.png'）")
    ap.add_argument("--dpi", type=int, default=96)
    ap.add_argument("--expect-pages", type=int, default=None,
                   help="设计页数（.page 段数）。物理页数 != 此值 = FAIL")
    # 留白阈值（默认偏宽：居中设计的"明信片风"留白 ≥15% 也常见；
    # 真正要严卡的是"居中差"——top ≈ bottom。绝对阈值是 sanity check 不是主闸。）
    ap.add_argument("--max-bottom", type=float, default=20.0, help="非尾页页底留白上限(%)")
    ap.add_argument("--max-tail", type=float, default=25.0, help="尾页页底留白上限(%)")
    ap.add_argument("--max-top", type=float, default=20.0, help="非尾页页顶留白上限(%)")
    ap.add_argument("--max-tail-top", type=float, default=25.0, help="尾页页顶留白上限(%)")
    # 居中阈值
    ap.add_argument("--max-center-delta", type=float, default=5.0,
                   help="页顶 vs 页底留白差(%)上限；超过 = 未居中")
    # 贴边阈值
    ap.add_argument("--min-edge", type=float, default=8.0,
                   help="左右内容到纸边的最小距离(px)；低于 = 贴边")
    ap.add_argument("--skip-cover", action="store_true", default=True, help="跳过第 1 页（封面）")
    ap.add_argument("--no-skip-cover", dest="skip_cover", action="store_false")
    # 页面尺寸硬闸（手机竖屏 9:20 ≈ 300×667.5pt）——非此尺寸/横版 = FAIL。
    # 通用化时用 --expect-w/--expect-h 换目标，或 --any-size 关闭本闸。
    ap.add_argument("--expect-w", type=float, default=300.0, help="期望页宽(pt)，默认 300")
    ap.add_argument("--expect-h", type=float, default=667.5, help="期望页高(pt)，默认 667.5")
    ap.add_argument("--size-tol", type=float, default=12.0, help="尺寸容差(pt)")
    ap.add_argument("--any-size", action="store_true", help="关闭页面尺寸硬闸（通用/非手机竖屏时用）")
    args = ap.parse_args()

    tmp = None
    if args.src.lower().endswith(".pdf"):
        tmp = tempfile.mkdtemp(prefix="checkedges_")
        pngs = render_pdf_to_pngs(args.src, args.dpi, tmp)
    else:
        pngs = sorted(glob.glob(args.src)) or sorted(glob.glob(args.src.replace("%d", "*")))
        if not pngs:
            raise SystemExit(f"找不到 PNG：{args.src}")

    # 页面尺寸硬闸：非 ≈期望竖屏尺寸（如被做成 A4/横版）直接判失败
    size_bad = False
    if args.src.lower().endswith(".pdf") and not args.any_size:
        sz = page_size_pt(args.src)
        if sz:
            w, h = sz
            if abs(w - args.expect_w) > args.size_tol or abs(h - args.expect_h) > args.size_tol:
                size_bad = True
                orient = "横版" if w > h else "尺寸不符"
                print(f"✗ 页面尺寸 {w:.0f}×{h:.0f}pt ≠ 期望 {args.expect_w:.0f}×{args.expect_h:.0f}pt（{orient}）"
                      f" ——手机竖屏 9:20 必须用 render_mobile.cjs 出 300×667.5pt，别改成 A4/横版。")

    n = len(pngs)
    print(f"页数：{n}  "
          f"阈值：底留白 ≤{args.max_bottom:.0f}% / 顶留白 ≤{args.max_top:.0f}% / "
          f"居中差 ≤{args.max_center_delta:.0f}% / 左右边距 ≥{args.min_edge:.0f}px")

    page_count_bad = False
    if args.expect_pages is not None and n != args.expect_pages:
        page_count_bad = True
        print(f"✗ 页数溢出：物理页 {n} ≠ 设计页 {args.expect_pages}"
              " ——有 .page 段没塞进一页（内容超一页/塌页）。先修到页数相等再看边距。")

    print(f"{'页':>3} {'顶%':>6} {'底%':>6} {'Δ':>5} {'左px':>5} {'右px':>5}  判定")
    fails = []
    for i, p in enumerate(pngs, 1):
        top_pct, _ = edge_whitespace_pct(p, 'top')
        bot_pct, _ = edge_whitespace_pct(p, 'bottom')
        left_px = content_edge_distance_px(p, 'left')
        right_px = content_edge_distance_px(p, 'right')
        center_delta = abs(top_pct - bot_pct)
        is_cover = (i == 1 and args.skip_cover)
        is_tail = (i == n)

        if is_cover:
            verdict = "SKIP"
            note = "封面"
        else:
            top_limit = args.max_tail_top if is_tail else args.max_top
            bot_limit = args.max_tail if is_tail else args.max_bottom
            problems = []
            if top_pct > top_limit:
                problems.append(f"顶留白{top_pct:.0f}%>{top_limit:.0f}%")
            if bot_pct > bot_limit:
                problems.append(f"底留白{bot_pct:.0f}%>{bot_limit:.0f}%")
            if center_delta > args.max_center_delta:
                problems.append(f"未居中Δ{center_delta:.0f}%>{args.max_center_delta:.0f}%")
            if left_px < args.min_edge:
                problems.append(f"左贴边{left_px:.0f}px<{args.min_edge:.0f}px")
            if right_px < args.min_edge:
                problems.append(f"右贴边{right_px:.0f}px<{args.min_edge:.0f}px")
            if problems:
                verdict = "FAIL"
                fails.append((i, " / ".join(problems)))
            else:
                verdict = "OK"
        print(f"{i:>3} {top_pct:>5.1f}% {bot_pct:>5.1f}% {center_delta:>4.1f}% "
              f"{left_px:>4.0f}px {right_px:>4.0f}px  {verdict} {note if is_cover else ''}")

    print()
    if fails or page_count_bad or size_bad:
        if size_bad:
            print("✗ 页面尺寸不合格（见上）——用 render_mobile.cjs 重出竖屏 300×667.5pt。")
        if page_count_bad:
            print(f"✗ 页数溢出：物理页 {n} ≠ 设计页 {args.expect_pages}。")
        if fails:
            for i, reason in fails:
                print(f"✗ 第 {i} 页：{reason}")
        print("修完重渲染再跑本脚本。")
        raise SystemExit(1)
    print("✓ 验证门「页边距+居中」通过（仅边距维度；孤立标题/破图/CORS 仍须看图核对）。")


if __name__ == "__main__":
    main()
