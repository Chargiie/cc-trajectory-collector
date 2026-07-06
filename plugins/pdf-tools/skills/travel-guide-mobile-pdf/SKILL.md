---
name: travel-guide-mobile-pdf
description: >-
  生成适合手机竖屏阅读、可分享的旅游攻略 PDF（HTML→Chromium→PDF）。
  支持两种设计单元：.page（默认，一段一物理页，自然流动）+ .screen（opt-in，固定一屏的硬卡）。
  当用户要「旅游/citywalk/周末出游/行程攻略」且要「手机看的 PDF / 可分享卡片」时使用。
  不适用于桌面打印 A4 报告、纯文本回答。
---

# 旅游攻略手机 PDF Skill

把旅游攻略渲染成**手机竖屏、可分享**的 PDF。本文件管**流程、工具链、机械契约、验证门**；**视觉样式不在这里约束，留给模型自由发挥**——调性、配色、版式按场景自己定，**不要照搬模板**。

## 两类设计单元（按需选，不冲突）

本 skill 渲染任意外层标签名为 `.page` 或 `.screen` 的 `<section>`，**两种类名混用也支持**。什么时候用哪个：

| 类名 | 行为 | 用在 |
|---|---|---|
| **`.page`**（默认） | 一段一物理页，自然流动分页；内容不够高就用 `min-height: 1 page` + flex center 撑起 + 垂直居中 | 90% 的内容页：行程总览 / 某天路线 / 预算表 / 实用贴士 / 时间轴… |
| **`.screen`**（opt-in） | `height: 1 page` 固定 + 渲染时溢出检测；强制 1 卡 = 1 物理页，不许内容溢出 | 满版封面图（要正好填满一屏）、单张可分享的「卡」（截图发出去独立成立）、缩略图列表 |

**判断口诀**：
- "这一页放的是一整段内容" → `.page`
- "这一页必须正好一屏高，少了空、多了切" → `.screen`

两者**不互斥**：一份文档可以混用——比如封面用 `.screen`（满版图），其余内容用 `.page`（自然流动）。renderer 两种都校验。

> **⚠️ 严禁全部用 `.screen`！** 实测踩坑：模型容易把所有页都设为 `.screen`（固定高度 890px），导致内容反复溢出 → 被迫多轮压缩字号/间距 → 最终产物字小、拥挤、可读性差。**正确做法：封面 1 张 `.screen`，其余全部用 `.page`。** `.page` 用 `min-height` + `flex center` 居中，内容自然流动，不会溢出裁切，排版松弛。
>
> **决策规则（if-then）**：
> - if 这页是满版封面图 → `.screen`
> - if 这页是独立分享卡（截图发出去要独立成立）→ `.screen`
> - **else → `.page`**（这是 90%+ 的情况）
>
> **绝不要因为"想让每页正好一屏"就全选 `.screen`**——`.page` + `min-height:890px` + `flex center` 本身就能实现一段一页 + 居中，而且允许内容自然撑开，不会静默裁切。

## When to use
- 旅游攻略 / citywalk / 周末出游 / 多日行程，出口是**手机**、要 **PDF 或可分享卡片**
- **不适用**：纯文本回答、桌面 A4 报告、数据驱动可复现文档（那走 reportlab）

## 技术栈
HTML → Chromium(Playwright) → PDF。强视觉攻略用 HTML+Chrome 最顺手；**不要**走 LaTeX/reportlab。
**地点 / 路线 / 通勤 / 天气**这类真实数据走 **amap MCP skill**（见下「地图数据」），别凭记忆编坐标和距离。

## 工作流程
1. **需求拆解**：人数 / 天数 / 出发地 / 预算 / 节奏 / 必去点 / 硬性要求（如「两套路线」「机酒价」）。
2. **信息核查**：分工——**「在哪、怎么走、天气」问 amap，「多少钱、好不好」问 step-search**。
   - **位置类（amap）**：`maps_text_search` 验真实店名 + 地址（防编造）→ `maps_search_detail`（用 POI `id`）取坐标 / 评分 / 营业时间 / 人均。
   - **价格 / 口碑 / 贴士（step-search，宁可多查）**：amap 缺的动态信息全走 **step-search**（`python3 <step-search 目录>/scripts/stepsearch.py "query"`；key 走 env `STEPFUN_API_KEY` 或 `~/.stepfun/skills/step-search/key.txt`，两者其一即可）。**每个主要点位 / 每天行程至少查一轮**，覆盖：门票价 / 最新营业时间 / 必吃与人均 / 预约与订房 / 避坑注意 / 季节与实时开放状态 / 交通贴士。**调用宁多勿少**——攻略里每一处「多少钱、好不好、要不要预约、几点开」都应有 step-search 依据，**禁编造**；无 live 价标「估算 + 时间」。
   - 配图同理——**下载真实图到本地再用相对路径引用**（见机械契约「图片默认下载到本地」），别 hot-link 远程 URL（CORS 会静默破图）。
3. **排点 / 通勤 / 天气（amap）**：
   - 相邻点 `maps_direction_walking`（citywalk 必备）、跨区 `maps_direction_transit_integrated` / `maps_direction_driving` 出真实「距离 + 时长」；`maps_distance` 批量测距判断「这天塞不塞得下」、给点位排序。**写进攻略的通勤数字必须来自这些调用。**
   - `maps_weather` 查目标城市预报 → 攻略开头给室内外 / 带伞 / 穿衣建议，必要时据此调路线。
   - amap 调不通时降级 step-search 并标「估」，**绝不编造坐标 / 距离**（见下「地图数据」）。
4. **分页（先定页数）**：先决定**这份攻略分几页、每页放哪个小标题 + 哪些内容块**。`.page` 走自然分页（内容不够高就居中），`.screen` 必须 1 卡 = 1 页。**先把页数定下来再写 HTML，别一上来就写满**。
5. **每页定密度档 + 适配排版**：估这页内容量 → 挑密度档，按档调字号 / 行距 / 间距 / 布局：
   - **稀疏**（封面 / 章节卡 / 单点 hero）：少元素 → 大字、大留白、居中焦点、一个视觉主角。
   - **均衡**（标准点位卡 / 2–3 块）：中字号、舒适间距、卡片化。
   - **密集**（路线时间轴 / 长清单 / 对照表）：紧凑字号行距（但守可读下限）、小间距、必要时分栏。
   - **同一页内容多就上密集档、少就上稀疏档——参数跟着内容量走，别一套参数套全程。**
6. **写 HTML**：每段一个 `<section class="page">`（默认）。需要硬卡（满版封面 / 独立可分享单卡）才改用 `<section class="screen">`。renderer 靠类名做护栏，**务必用 `.page` 或 `.screen` 这两个类名之一**，别用别的。
7. **渲染 PDF**：`node scripts/render_mobile.cjs <源>.html <产物>.pdf`（需 `NODE_PATH` 指向装了 playwright 的 node_modules）。读它输出的**逐页报告**。
   - **产物命名（重要）**：文件名用 **query 关键词**，**禁用** `out.pdf` / `guide.pdf` / `攻略.pdf` 这类通用名（易重名、难分辨）。例：query「南京老门东半天拍照」→ `南京老门东citywalk.pdf`；HTML 中间文件同名（`南京老门东citywalk.html`）。
   - **批量跑、且有 `query_id` 时**：**优先用 query_id 命名**（如 `q042.pdf`，或 `q042_南京老门东.pdf` 兼顾可读+唯一），保证不重名、可回溯到 query。
8. **自检 → 重修闭环（不过不交付）**：跑 `python3 scripts/check_edges.py <产物>.pdf --expect-pages <设计页数>` 测页边距 + 居中；再 `pdftoppm -r 96 -png <产物>.pdf $TMPDIR/pg` **逐页 Read 看图**核对脚本测不到的（孤立标题、破图、卡片切断、贴边）。**任一不过 → 写清哪页哪元素什么问题 → 改 HTML → 重渲染 → 重跑。循环到全过。**
9. **交付（必须附自检证据，否则不算完成）**：**逐页列证据表**——每页：`check_edges` 留白% / 居中 / 贴边 / 有无孤立标题/破图/切断。**没有这张表 = 没做自检 = 不准交付**。

## 机械契约（渲染正确性，非样式偏好）
不照做会出坏产物，和审美无关，必须遵守：

**页面结构**
- **页面尺寸**：目标设备 2670×1200（≈9:20 竖屏，DPR3 → 逻辑视口 ≈400×890 CSS px）。渲染器用 `width:'400px' height:'890px'`（= 300pt×667.5pt；**Playwright `page.pdf` 不收 `pt` 单位**，必须用 px/in）。
- **满版无白边**：渲染 `margin:0`、`displayHeaderFooter:false`；页边距 / 留白全靠 **CSS 内边距**，别靠渲染器 margin。
- **背景跨页连续**：背景色铺到 `html, body`；多页时翻页处**不能出现白条 / 断裂**。
- **`.page` 排版**（默认范式）：每个设计页 = 一个 `<section class="page">`；**用 `min-height: 1 page` + `flex center` 垂直居中**（居中后留白被分到上下两端，物理页数==`.page` 段数，验证门会同时核对）。**不设 `height`，不设 `overflow:hidden`**——高度由内容自然撑开，超出才会被物理分页、`check_edges --expect-pages` 抓到。
- **`.screen` 排版**（opt-in）：每个硬卡 = 一个 `<section class="screen">`；`height:890px` 写死 + `overflow:hidden` 兜底；**`render_mobile.cjs` 渲染时逐卡测内容高，超一屏 → 退出码 1 + 指出卡号**。**只在必须"1 卡=1 屏"时用**（满版封面、独立分享卡）。
- **居中机制（`.page`）**：`.page:not(.cover){ min-height:890px; display:flex; flex-direction:column; justify-content:center; }`——`min-height` 允许内容撑高溢出（`check_edges` 能抓到），不像 `height+overflow:hidden` 那样静默裁掉。
- **兜底（不被切断）**：`.page` / `.screen` 都给**叶子卡片**（`.tip-card` / `.place-card` / `.timeline-item` 等）`break-inside: avoid`、给**所有标题 / 小标题**（`.day-header` / `.sec-title` / `.label` / `.section-title` 等）`break-after: avoid`——`check_edges` 测不到的卡片切断 / 标题孤立靠这个兜底。
- **不横向滚动**：内容宽 ≤ 内容区宽；长 URL / 表格换行或缩短。

**内容真实**
- **链接必须真可点**：地图 / 订房 / 参考用真实 `<a href>`（渲染成 PDF `/Link`），**禁假链接 div**。
- **图片默认下载到本地再引用（首选策略，根治 CORS）**：找到真实图 URL 后，**先 `curl`/`wget` 下载到 `workspace/img/`，HTML 用相对路径 `img/xxx.jpg` 引用**——渲染时是本地文件、不发跨源请求，**CORS 从根上不存在，实拍照片稳定显示**。这是默认做法，**不是兜底**。**别直接 hot-link 远程 URL**（即使 `curl` 200 也可能被 CORS 静默拦成破图，见下）。
- **图搜链：SERP 图搜 → amap POI 图 → CSS 手绘。⛔ 图搜不用 stepsearch，不用 Wikimedia，不用 openverse（本环境不可达，已剥离）。**（stepsearch 只管价格/口碑/营业时间等**文本**,不拿它搜图；坐标/路线/天气仍走 amap，见「地图数据」——这条只管**配图**。）按序:
  - ① **SERP 图搜(首选,质量/分辨率最优)**——`python3 <serp-image-search skill 目录>/scripts/serp_image_search.py search "<关键词>" --min-long-edge 1200 --top 5`,返回候选(source / 分辨率 / `origin_image_url`)。实测拍摄角度、构图、分辨率均优于 amap(封面常有 1280–1700px 专业/媒体图);**封面/hero 挑长边 ≥ ~1200px**,`download` 子命令或 `curl` 下到本地再引用。
  - ② **amap POI 实拍(SERP 没有/不够贴题时兜底,尤其小众店铺)**——`maps_search_detail` 的 `photo` 字段(`store.is.autonavi.com/showpic/...`)是该店/景点真实照片,在地感强、文件小。**注意多为评论区 UGC,分辨率参差(常 375–500px 缩略),拍摄角度不稳**;要高清可改评论 CDN 尺寸参数(`aos-comment.amap.com/.../comment/<hash>_2048_2048_80.jpg` 的 `_2048_2048_80` 段可调到 2048px),但仍不如 SERP 稳。内页/点位小图用 amap 缩略够清晰。(量尺寸:PIL / macOS `sips -g pixelWidth -g pixelHeight`、Linux `identify`。)
  - ③ **CSS/SVG 手绘(前两者都拿不到才走)**——见降级方案。
  **别凭记忆编 URL**;下载后一律以渲染后逐页看图为准。
  - **⚠️ 体积:SERP 高清图会显著撑大 PDF(实测封面级 1700px 图能把 8 页 PDF 撑到 ~38M)。分享前压缩或限长边**(如封面 ≤1600px、内页 ≤1000px;`sips -Z 1600 <f>` 或 `magick <f> -resize 1600x1600\> <f>`),避免文件过大难分享。
- **CORS 陷阱(为什么要下载而非 hot-link)**:`curl -sI` 200 是**必要不充分**——很多图床(amap `aos-comment.amap.com` / `store.is.autonavi.com` 等)**不返回 `Access-Control-Allow-Origin` 头**,Chromium 渲染 hot-link 的远程图时会被 CORS **静默拦成破图**(`curl` 查不到,CORS 是浏览器层)。**下载到本地引用就绕开整个问题**——这正是默认下载的原因。另:`<img>` 除非要 canvas 读像素,**一律别加 `crossorigin="anonymous"`**(会把本可加载的图强制转成 CORS 请求)。
- **降级方案(amap 拿不到够清晰的图才走,不是嫌麻烦的逃生门)**:**严禁 picsum.photos / placeholder.com 占位图**(随机图与目的地无关,比无图更差)。顺序:① 减到 1–2 张 hero **真图**,其余页用文字 + 排版撑视觉;② 仍无图才用 CSS `linear-gradient`/pattern/emoji/内联 SVG 做视觉替代;③ 绝不留空白图框(`<img>` 会 404 就删掉或换 CSS)。**⚠️ CSS 手绘是最后手段——默认应尽力用真实照片(SERP 首选、amap 兜底),实拍产物质量显著高于全插画(实测同一 query,实拍 vs 全 CSS 插画观感差一档)。**
- **唤端链接用 web URI**（PDF 链接点击最稳）：地图 / 导航 / 订房用 **`https://uri.amap.com/...`** web URI；**别用** `amapuri://...` 唤端 scheme——PDF 阅读器未必识别，点不动还以为是死链。
  - 单点标注：`https://uri.amap.com/marker?position=<lng>,<lat>&name=<名称>&coordinate=gaode&src=guide`
  - 导航到某点：`https://uri.amap.com/navigation?to=<lng>,<lat>,<名称>&mode=walk&coordinate=gaode&src=guide`（`mode` 取 `walk`/`car`/`bus`）
  - `<lng>,<lat>` 来自 amap（`maps_geo` 或 `maps_search_detail`），名称做 URL 转义。
- **坐标 / 距离 / 通勤真实**：攻略里的坐标、点间距离、步行 / 通勤时长**必须来自 amap 调用**（`maps_direction_*` / `maps_distance`），**禁手填猜的数字**——和「图片真实可达」同理，编的距离 = 坏产物。amap 调不通时降级为「约 X 分钟（估）」并标注，不要写成精确值。

**渲染细节**
- **CJK 字体栈 / 回退**：`-apple-system, "PingFang SC", "STSong", "Helvetica Neue", Arial, sans-serif`，回退顺序 `SC→JP→KR→Latin`（日文在前会让简体用日文字形），字型最多两种；webfont 截图前 `await document.fonts.ready`。
- **颜色保真**：`print-color-adjust: exact` 挂 `*`（挂 body 无效）。
- **图表少用**：攻略一般不用图表；万一用 Chart.js，必须 `animation:false` 且等渲染完再截。

## 地图数据（amap MCP）
真实地点 / 坐标 / 路线 / 通勤 / 天气走 **amap MCP skill**（`mcp__amap__maps_*` 工具）。编排纪律：
- **坐标先行**：要算路线 / 测距前先拿 `经度,纬度`。规范**地址** → `maps_geo`；**POI / 店名 / 景点** → `maps_text_search` 拿 `id`（不返回坐标）→ `maps_search_detail` 取 `location`。**别手填猜的坐标**。
- **分工**：位置 / 路线 / 天气问 amap；价格 / 订房 / 口碑 / 贴士问 **step-search**（`scripts/stepsearch.py`，key 走 env `STEPFUN_API_KEY` 或 `~/.stepfun/skills/step-search/key.txt`）——**宁可多查**，见工作流「信息核查」。
- **失败兜底**：调用报错（如 `ENGINE_RESPONSE_DATA_ERROR`）→ POI 改 `text_search`、补全必填参数重试；仍不行 step-search 兜底并标「估」，**不要编造坐标 / 距离**。
- **链接**：用高德 web URI（见机械契约「唤端链接用 web URI」），不用 `amapuri://` 唤端 scheme。
- **未注册兜底**：运行环境没把 amap MCP 工具暴露给模型时，走 amap skill 的同名兜底脚本——`python3 <amap-mcp skill 目录>/scripts/mcp_call.py <工具名> '<json 参数>'`（key 解析：`AMAP_MCP_KEY` 环境变量 → `scripts/key.txt`）。

## 渲染器（scripts/render_mobile.cjs）
满版无白边（margin 0，留白交给 CSS），并**对 `.page` 段数 == 物理页数** + **`.screen` 卡内容高 ≤ 一屏**做护栏：
- `.page` 段数与物理页数不符 → 退出码 1（说明有段超一页被拆 / 塌页）。
- 任一 `.screen` 内容超 890px → 退出码 1 + 指出卡号。
- `.screen` 填充 < 55% 给 warn。
- 无 `.page` / `.screen` 时回退普通流动渲染（向后兼容，护栏不生效）。
用法：
```bash
NODE_PATH=<装了 playwright 的 node_modules> node scripts/render_mobile.cjs source.html out.pdf
```
输出 JSON 含 `pages`（.page 段数）、`screens`（.screen 卡数）、物理页数 `pdf_pages`、逐卡 `contentH/fill/overflow`、违例清单 `overflow_screens` / `page_count_mismatch` / `underfill_screens`。**任一非空必须改到空再交付。**

## 页边距 + 居中硬闸（scripts/check_edges.py）
把验证门的「留白不过量」+「未居中」+「贴边」从主观目测变成**硬数字**：
- **页顶 / 页底留白%**（基于行内自身均匀度，不依赖全局背景色）；默认非尾页 ≤20% / 尾页 ≤25%（居中设计的"明信片风"留白 ≥15% 也常见，**绝对阈值是 sanity check 不是主闸**）
- **居中差**（top - bottom 绝对值）：默认 ≤5%；**这才是硬闸**——top ≈ bottom 才算"居中"，不管留白多少
- **左右边距**到内容边距离 ≥8px（不贴边）
- 失败 → `exit 1` + 指明页号 + 现象

```bash
python3 scripts/check_edges.py out.pdf --expect-pages <设计页数>  # 页数 != 设计 → FAIL
python3 scripts/check_edges.py out.pdf --max-bottom 10 --max-top 10 --max-center-delta 3 --min-edge 12
# 想要"满版"严卡（不留呼吸感）：把 --max-top/--max-bottom 调到 10
# 想要"超宽松明信片风"：调到 30
```

**它只管页边距 + 居中 + 贴边；孤立标题 / 破图 / 卡片切断测不到，仍须逐页看图。**

## 验证门（流程纪律，缺一不可 — 不过就重修，循环到过）
**这是一个闭环，不是一次性勾选**：任一项 ✗ → 写清问题（页号+元素+现象）→ 改 HTML → 重渲染 → 重跑全部检查。**全 ✓ 才算完成。**

| 检查 | 命令 / 方法 | 通过标准 | 不过怎么修 |
|---|---|---|---|
| 页面尺寸（硬闸） | `check_edges.py`（内置，自动） | ≈300×667.5pt 竖屏（9:20）；横版/A4/尺寸不符 = FAIL | 用 `scripts/render_mobile.cjs` 重出（通用其它尺寸用 `--expect-w/-h` 或 `--any-size`） |
| **页数 == 设计页数** | `check_edges.py --expect-pages <N>` | 物理页数 == `.page` 段数 | 有段超一页被拆 → 拆段 / 降密度；塌页 → 查固定高度+overflow:hidden |
| **页边距 + 居中** | `check_edges.py` | 页底留白 ≤10% / 尾页 ≤15%；上下差 ≤5%；左右 ≥8px | 补内容 / 上提后文 / 调 CSS 内边距；偏 → 调 `flex center` |
| 无 `.screen` 溢出 | `render_mobile.cjs` 报告 | `overflow_screens` 为空、退出码 0 | 拆卡 / 降密度 / 缩字号 |
| `.screen` 填充合理 | 渲染报告 `fill` + 看图 | 各卡 ≥55%；<55% 确认是有意 hero 否则补 | 加大字号 / 补内容 / 合并卡 |
| 图片未丢 | `pdfimages -list out.pdf \| tail -n +3 \| wc -l` | ≥ 预期数（404/CORS 失败都会漏图） | **下载到本地用相对路径引用**（根治 CORS，见「图片默认下载到本地」）；破图必换 |
| 链接可点 | `pypdf` 数 `/Link` + spot-click ≥3 | 命中、能跳转；**不含 `amapuri://` 唤端 scheme** | 补真实 `<a href>`；唤端链接换 web URI |
| 距离/通勤真实 | 核对行程里的距离 / 时长 | 来自 amap `direction_*` / `distance`，非手填猜值；调不通处已标「估」 | 补 amap 调用或标「估」 |
| 地图链接可开 | spot-check `uri.amap.com` 链接 | 浏览器能打开、落点正确；无 `amapuri://` 唤端 scheme | 换高德 web URI |
| 文字可读 | `pdftotext -layout` | 无乱码、无残留占位符 | 修字体栈 / 占位符 |
| 翻页连续 | 看图 | 背景跨页连续、无白条断裂 | 背景铺 `html, body` |
| 块不被切断 | 看图 | 卡片 / 行程项 / 标题都不跨页断裂；标题不孤立页底 | 叶子卡片 `break-inside:avoid` + 标题 `break-after:avoid`（兜底） |

> **看图是主验证手段**：渲染后必跑 `pdftoppm -r 96 -png out.pdf $TMPDIR/pg`，用 **Read 逐页看图**核对分页/溢出/贴边/居中——`pdfinfo` / `pdftotext` / `check_edges` 全部测不到图片是否真显示（CORS 破图 / 字体丢字 / 截图被装饰挡住）。**页底留白是反复出现的头号缺陷，看图时逐页量一眼留白比例。**
>
> `pdftotext` 看不到图像，单独用会**静默放行无图 PDF**——必须配 `pdfimages`。任何步骤 **MUST NOT** `2>/dev/null`（丢掉唯一的失败信号）。

## 样式（自由发挥，不在本文件约束）
调性、配色、卡片 / 标签 / callout / emoji、字号行距——**模型按场景与每页密度档自由发挥，不要照搬任何模板**。只要过上面的机械契约和验证门即可。
