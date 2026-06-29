# pdf-tools

旅游攻略**手机竖屏 PDF** 生产三件套，一个 plugin 打包三个 skill：

| skill | 作用 |
|---|---|
| `travel-guide-mobile-pdf` | HTML→Chromium→PDF；页卡范式（`.page`/`.screen`）+ 渲染期溢出守卫 + `check_edges.py` 版面硬闸自检 |
| `amap-mcp` | 高德地图 MCP：POI/坐标/路线/通勤/天气；未注册 MCP 时走兜底脚本 |
| `step-search` | StepFun 联网搜索（价格/口碑/订房等 amap 弱项） |

## 安装

```bash
claude plugin marketplace add Chargiie/cc-trajectory-collector
claude plugin install pdf-tools
```

## 前置依赖（装完才能真出 PDF）

- **Node.js + Python 3**
- **Playwright + Chromium**（`travel-guide-mobile-pdf` 渲染用）：
  ```bash
  npm i -g playwright && npx playwright install chromium
  # render 时 NODE_PATH 指向全局 node_modules，如 ~/.npm-global/lib/node_modules
  ```
- **`STEPFUN_API_KEY`**（`step-search`）：`export STEPFUN_API_KEY=<key>`（见 step-search/SKILL.md「Key 配置」）
- **`AMAP_MCP_KEY`** 或已注册 amap MCP（`amap-mcp`）：`export AMAP_MCP_KEY=<key>`（见 amap-mcp/SKILL.md）

> 缺 key 时对应 skill 会干净报错并降级（如搜索/地图调不通改标「估」），不会崩。

## 来源 / 同步

三个 skill 的 canonical 源在 `Chargiie/all_skills`；本目录是分发快照，跟随 all_skills 更新。
