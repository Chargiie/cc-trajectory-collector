# [标注者文档] 类PDF Agent 轨迹数据生产

| 修订版本 | 待标数据 | 修订备注 | 采集验收 |
|---|---|---|---|
| 1.0 | 一批"类PDF攻略/复杂生成"类 query | 初版需求撰写 | —— |

> 工具:`trajectory-collector`(见仓库 README)。本文件指导标注/采集人员如何用该工具批量生产**合格**的 SFT 轨迹数据。

---

## 背景

我们需要一批**复杂、多工具、多轮**的真实 Claude Code(`sc claude`)agent 轨迹,作为 SFT 训练数据。典型任务是"做一份手机版 PDF 攻略"这类**长链路生成任务**——需要联网查数据(amap/WebSearch)、规划、写文件、渲染 PDF、自检迭代。

`trajectory-collector` 在 `base_url` 外包一层日志代理,真实跑一遍并产出标准 SFT 格式轨迹(含完整 system prompt、tool schema、明文 thinking、多轮 tool_call/结果)。本文件规定**每条数据如何采集、如何判定合格、不合格如何处理**。

---

## 需求概况

1. **待标数据**:一批 query(类PDF攻略 / 复杂生成类),每条 query = 一个采集任务。
2. **标注目标**:对每条 query,用工具产出**一条合格的** `trajectory_sft.json`(+ 附带 `trajectory.html`、生成的 PDF 等产物),并完成质量校验后归档。
3. **人力/环境**:每人一台已配置好 `sc`(stepcode/Claude Code,已鉴权)+ Node + Python3 的机器;能跑通 `sc claude`。
4. **产物交付**:每条 query 对应一个 `runs/<时间戳>_<slug>/` 目录,核心交付物 `trajectory_sft.json`;`calls.jsonl`(含 token)**不外传**。

---

## 标注方法及详细规则

### 操作步骤

1. **环境预检**(每批次开始前一次即可):
   ```bash
   cd ~/Desktop/trajectory-collector
   node -v && python3 -V                       # 依赖在
   sc system baseurl                            # 应为 https://models-proxy.stepfun-inc.com
   pgrep -fl 'proxy.js|claude-opus' || echo 干净  # 无上一轮残留进程
   ```
   > 若 `baseurl` 不是上游地址(被上一轮异常中断留在了 `127.0.0.1:...`),先还原:
   > `sc system baseurl https://models-proxy.stepfun-inc.com`

2. **采集一条**:
   ```bash
   python3 collect.py "<这条 query 原文>"
   ```
   - **默认不限时**,跑到模型自然收尾(`end_turn`)为止——复杂/多页生成任务不会被中途截断。
   - 如需封顶时间,加 `--max-seconds N`(秒)。
   - ⚠ 无限时模式下,若遇**上游流截断**或 **`AskUserQuestion` 反问**,会**一直挂着**,需人工 `Ctrl-C` 中止后按「负向规则」重跑。
   - 工具会自动:起专属代理 → 临时切 `sc baseurl` 到代理(结束自动还原)→ 在隔离 `workspace/` 全自动跑 → 重建轨迹 → 转 SFT → 生成 HTML → 清理本 run 的 memory 命名空间。

3. **看结束状态**(命令末尾打印的 `[session]`):
   - `reason=completed` → 正常收尾,进入质检。
   - 长时间无进展(挂死)→ 多半是上游截断或反问,`Ctrl-C` 后见下方「负向规则」重跑。

4. **人工质检产物**:
   - 打开 `runs/<...>/trajectory.html`(浏览器)通读对话:任务是否真完成、工具用得是否合理。
   - 打开生成的产物(如 `workspace/*.pdf`)用**预览/Preview** 核对:**页数、内容、样式**是否符合 query 要求(注意:阅读器可能停在第 1 页,需翻页确认全部页数)。

5. **校验 SFT 格式**(`trajectory_sft.json`):
   - 顶层键 = `session_id / agent / session_key / tools / messages`;`tools[i]` 含 `parameters`。
   - 角色序列连贯:`system → user → assistant(tool_calls) → tool → …`;每个 `tool` 的 `tool_call_id` 能在前面 assistant 的 `tool_calls[].id` 找到配对。
   - `assistant` 步有 `reasoning_content`(明文 thinking)、`loss_mask`。

6. **归档 / 处置**:合格 → 保留该 `runs/<...>/` 目录(至少 `trajectory_sft.json`)并按交付要求归档;不合格 → 按「负向规则」重跑或丢弃,并记录原因。

### 标注规则

> 判定一条轨迹是否**合格可用**。建议先拿 1~2 条走通全流程作为示例参考。

- **合格(正向)**:同时满足——
  - `reason=completed`(模型以 `end_turn` 自然收尾);
  - 任务**真完成**,产物(PDF 等)**真生成**且符合 query 要求;
  - **多轮真实工具调用**(查数据/写文件/渲染等),非空转;
  - assistant 步含**明文 thinking**(`reasoning_content` 非空,至少部分步有);
  - SFT 角色序列完整、`tool_call_id` 全部配对;
  - **无上下文污染**:system prompt 里**没有**混入 `MEMORY.md` 内容或祖先 `CLAUDE.md`(采集时若 `[preflight][warn]` 报了 CLAUDE.md,需在备注里记录)。

- **负向规则**(命中任一即**不合格**,按括号内处置;并记录原因):
  - **流被截断卡死**:末条调用 `stop_reason=None`、无 `message_stop`(上游间歇性掐断);无限时模式下会一直挂。→ `Ctrl-C` 中止,**重跑同一 query**(偶发,重跑大概率成功)。
  - **反问挂起**:agent 调用了 `AskUserQuestion`,无人应答导致干等(会一直挂)。→ `Ctrl-C` 后重跑;若反复出现,改写 query 让需求更明确,或丢弃。
  - **未完成就中止**:若你手动设了 `--max-seconds` 且 `reason=max_seconds`、产物不完整。→ 去掉时限或加大重跑。
  - **产物缺失/无效**:声称做了 PDF 但 `workspace/` 没有产物,或 PDF 页数/内容明显不符 query。→ 丢弃或重跑。
  - **空转/低质**:大量工具调用但无实质进展、最终没达成任务。→ 丢弃。
  - **上下文污染**:system prompt 注入了历史 memory / 祖先 CLAUDE.md(说明隔离失效)。→ 不合格,排查 cwd 唯一性后重跑。

- **如何区分"截断"与"反问"**(两种都表现为卡住):
  - 截断:末条响应**不完整**(无 `message_stop`、`stop_reason=None`)。
  - 反问:末条响应**完整**(有 `message_stop`)且最后一个工具是 `AskUserQuestion`。

---

## 附:质检速查清单(每条过一遍)

- [ ] `[session]` 显示 `reason=completed`
- [ ] 运行结束后 `sc system baseurl` 已还原为上游地址
- [ ] `trajectory.html` 通读:任务真完成、工具合理
- [ ] 产物(PDF 等)用 Preview 核对页数/内容/样式符合 query
- [ ] `trajectory_sft.json`:角色序列完整、`tool_call_id` 配对、`reasoning_content` 有明文
- [ ] 无截断(末条有 `message_stop`)、无 `AskUserQuestion` 挂起
- [ ] 无上下文污染(无 MEMORY.md / 祖先 CLAUDE.md 注入)
- [ ] `calls.jsonl` 含 token,**未外传 / 未入库**
