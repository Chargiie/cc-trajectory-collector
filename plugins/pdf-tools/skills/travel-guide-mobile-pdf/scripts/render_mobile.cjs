// 手机竖屏 PDF 渲染：400×890 CSS px ≈ 300×667.5pt（9:20，匹配 2670×1200 手机屏）
// 满版无白边：margin 0，留白靠 CSS 内边距，背景色跨页连续。
// 双范式护栏：
//   - `.page`（默认）：计段数 vs 物理页数；不等 → 退出码 1（说明有段被拆 / 塌页）
//   - `.screen`（opt-in）：固定一屏高，逐卡测内容是否溢出；溢出 → 退出码 1 + 指明卡号
//   混用也支持：renderer 分别校验两类。
//   无 .page / .screen 时回退旧行为（仅渲染 + 报体积）。
// 用法：NODE_PATH=~/.npm-global/lib/node_modules node scripts/render_mobile.cjs source.html out.pdf

const { chromium } = require('playwright');
const path = require('path'), fs = require('fs');

const PAGE_H = 890;            // 一页逻辑高（CSS px）
const OVERFLOW_TOL = 2;        // 容差，避免 1px 取整误报
const UNDERFILL = 0.55;        // .screen 低于此填充比给 warn（不致命，交给「看图」终判）

(async () => {
  const src = process.argv[2], out = process.argv[3];
  if (!src || !out) { console.error('用法: render_mobile.cjs <source.html> <out.pdf>'); process.exit(2); }

  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(src), { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.waitForTimeout(300);

  // —— 双范式测量：.page 计段数，.screen 测每卡内容高 ——
  const measured = await page.evaluate(() => {
    const measureContentH = (el) => {
      // 内容真实高度：取所有「非绝对/固定定位」后代相对卡顶的最大下沿
      // 绝对定位多为装饰出血，排除以免误报
      const r = el.getBoundingClientRect();
      let contentBottom = 0;
      el.querySelectorAll('*').forEach(c => {
        const pos = getComputedStyle(c).position;
        if (pos === 'absolute' || pos === 'fixed') return;
        const b = c.getBoundingClientRect().bottom - r.top;
        if (b > contentBottom) contentBottom = b;
      });
      const cs = getComputedStyle(el);
      const padBottom = parseFloat(cs.paddingBottom) || 0;
      return Math.round(contentBottom + padBottom);
    };

    const pages = Array.from(document.querySelectorAll('.page'));
    const screens = Array.from(document.querySelectorAll('.screen'));
    return {
      pageCount: pages.length,
      pages: pages.map((el, i) => ({ i: i + 1, contentH: measureContentH(el) })),
      screens: screens.map((el, i) => {
        const r = el.getBoundingClientRect();
        return {
          i: i + 1,
          boxH: Math.round(r.height),
          clientH: el.clientHeight,
          scrollH: el.scrollHeight,
          contentH: measureContentH(el),
        };
      }),
    };
  });

  await page.pdf({
    path: out,
    width: '400px',                        // = 300pt（96dpi：pt×4/3），Playwright page.pdf 不收 pt 单位
    height: '890px',                       // = 667.5pt，物理尺寸等价，9:20
    printBackground: true,
    preferCSSPageSize: false,
    displayHeaderFooter: false,            // 满版编辑风：不注入页眉页脚，避免与满版色块冲突
    margin: { top: '0', right: '0', bottom: '0', left: '0' },  // 满版无白边，留白靠 CSS 内边距
  });

  await browser.close();

  const size_kb = Math.round(fs.statSync(out).size / 1024);
  const out_abs = path.resolve(out);
  const pdf_pages = await countPdfPages(out_abs);
  const hasGuard = measured.pageCount > 0 || measured.screens.length > 0;

  // —— 无 .page / .screen：回退旧行为 ——
  if (!hasGuard) {
    console.log(JSON.stringify({ ok: true, size_kb, pdf_pages,
      pageCount: 0, screens: 0,
      note: '未发现 .page / .screen 元素，按普通流动模式渲染；护栏未生效' }));
    return;
  }

  // —— .page + .screen 校验：物理页数必须 == .page 段数 + .screen 卡数 ——
  // 每张 .screen 强制 height=890px → 1 屏=1 页；每段 .page 设计为 1 物理页
  const expectedPages = measured.pageCount + measured.screens.length;
  const pageMismatch = expectedPages > 0 && pdf_pages !== expectedPages;

  // —— .screen 校验：每卡 contentH ≤ PAGE_H（写死高度 + overflow:hidden 静默裁切，渲染期必须能抓） ——
  const screenReport = measured.screens.map(c => {
    const overflow = c.contentH > PAGE_H + OVERFLOW_TOL;
    const fill = +(c.contentH / PAGE_H).toFixed(2);
    return { card: c.i, contentH: c.contentH, scrollH: c.scrollH, fill,
      overflow, underfill: !overflow && fill < UNDERFILL };
  });
  const overflowScreens = screenReport.filter(r => r.overflow);
  const underfillScreens = screenReport.filter(r => r.underfill);

  const ok = !pageMismatch && overflowScreens.length === 0;
  console.log(JSON.stringify({
    ok,
    size_kb,
    pdf_pages,
    pageCount: measured.pageCount,
    pages: measured.pages,
    page_count_mismatch: pageMismatch,
    screens: screenReport.length,
    page_h: PAGE_H,
    cards: screenReport,
    overflow_screens: overflowScreens.map(r => r.card),
    underfill_screens: underfillScreens.map(r => r.card),
  }, null, 2));

  if (pageMismatch) {
    console.error(`\n✗ 页数不符：设计 .page=${measured.pageCount} + .screen=${measured.screens.length} = ${expectedPages}，物理页=${pdf_pages}。`
      + '有段超一页被拆 / 塌页 → 拆段、降密度、查 .screen 误用 height+overflow:hidden。');
  }
  if (overflowScreens.length) {
    console.error(`\n✗ .screen 溢出：第 ${overflowScreens.map(r => r.card).join(', ')} 张卡内容超过一屏(${PAGE_H}px)，会被裁切/出血。`
      + ' → 拆成多张卡，或降一档密度。');
  }
  if (underfillScreens.length) {
    console.error(`\n⚠ .screen 偏空：第 ${underfillScreens.map(r => r.card).join(', ')} 张卡填充 < ${UNDERFILL}，可能显空。`
      + '请看图确认是否「有意的 hero 留白」，否则放大字号/加内容/合并卡。');
  }
  if (!ok) process.exit(1);
})();

// 数 PDF 物理页数（不去 pdfinfo 依赖，page.pdf 渲出来直接读 /Count）
async function countPdfPages(pdfPath) {
  const buf = fs.readFileSync(pdfPath);
  // 简单 /Type /Page 计数（不去 pdfinfo 依赖；和 readFileSync 在文件 < 几 MB 时足够）
  const txt = buf.toString('latin1');
  const m = txt.match(/\/Type\s*\/Page[^s]/g);
  return m ? m.length : 0;
}
