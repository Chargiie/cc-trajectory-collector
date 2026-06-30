# cc-trajectory-collector

> 来源:基于 fangfeiteng 的 trajectory-collector。本版在其上新增 **content-thinking hack(拿原始 CoT)** 与 **三项防泄漏修复**,并修了 stepcode 启动框占住 stdin 的问题(见 `lib/pty_driver.py`)。

把**一个 query** 用 `sc claude`(Claude Code)真实跑一遍,自动产出一条 **SFT 训练格式**的 agent 轨迹——含完整 system prompt、工具 schema、thinking、多轮工具调用与结果——并附一份聊天式 HTML 可视化。

---

## 1. 解决什么问题

本地 Claude Code 的会话 transcript(`~/.claude/projects/.../*.jsonl`)存了对话和 thinking,但**缺**两样训练必需的:

- 当轮真正发给模型的**完整 system prompt**
- 全部**工具的 input schema**

这两样是客户端每次请求现拼的,不落盘。本项目在 `base_url` 外包一层**本地日志反向代理**,把请求/响应原样 tee 下来补齐;并坚持**交互式(非 `-p`)**启动,保证上游下发 thinking(`-p` headless 抓到的 thinking 是空的)。

> ⚠ **thinking 的性质**:默认拿到的是 Claude 原生 extended thinking,即 **Anthropic 压缩过的「摘要」**,不是逐 token 原始推理(实测 `output_tokens` 远大于可见 thinking)。要**原始完整 CoT**,开本版新增的 `TC_THINKING_HACK`(见 §4)。

## 2. 工作原理

```
sc claude(交互式, PTY 驱动)
        │  base_url 指向本地代理
        ▼
本地日志代理 proxy.js (127.0.0.1:<port>)
        │  原样转发 + tee 落盘;hack 模式下还会改写主 loop 请求
        ▼
StepFun 上游 (models-proxy.stepfun-inc.com) → 模型后端
        │
        ▼
   calls.jsonl(完整请求+响应, SSE 自动重组)
        │  build_trajectory → to_sft → render_html
        ▼
trajectory_sft.json  +  trajectory.html
```

## 3. 产物格式

顶层 SFT 扁平格式(对齐 `vm-trajectory-export-*` 样本):

- `tools[]`:`{name, description, parameters}`(原 `input_schema` → `parameters`)
- `messages[]`:`system / user / assistant / tool` 四角色,`content` 均为字符串
  - `assistant`:`content` + `reasoning_content`(thinking)+ `tool_calls[]` + `loss_mask`
  - `tool`:`tool_call_id` / `name` / `content` / `is_error`

`reasoning_content` 来源:默认=原生 thinking 块(摘要);hack 模式=正文 `<thinking>` 拆出(原始 CoT)。

## 4. thinking 两种模式(本版重点)

靠 `TC_THINKING_HACK` 切换,**同一 run 只能其一**:

| | 默认(开关关) | hack 模式(`TC_THINKING_HACK=1`) |
|---|---|---|
| 原生 thinking | adaptive(开启) | **disabled** |
| `reasoning_content` 来源 | 原生 thinking 块 | 正文 `<thinking>` 拆出 |
| **内容性质** | **摘要**(非逐 token) | **原始完整 CoT**(带 "Wait/let me check") |
| system prompt | 纯原生 Claude Code | 被代理改写(注入 guidance) |
| 适用 | 要真实未干预的 CC 轨迹 | 要真实 CoT 做训练 |

**hack 原理**(参考 demo_hack_thinking):关掉原生 thinking,改让模型把 `<thinking>...</thinking>` 当**普通正文**输出,外层解析。具体由代理在转发前对**主 loop 请求**做三件事:

1. `thinking` 设 `{"type":"disabled"}`(并去掉 `output_config`);
2. system 末尾追加 guidance(要求"先一次性想清楚再做":单个 `<thinking>` 块在开头,之后正常回复 + 工具调用,末轮给完整答案);
3. **最后一条 user 消息**末尾追加 suffix,催模型输出 thinking 块。

`<thinking>` 以正文形式流转,Claude Code 自然把它留在历史里**回放**给后续轮(模型每轮都能看到自己之前的完整推理)。重建时 `build_trajectory` 再把 `<thinking>` 拆成 thinking 块 → `reasoning_content`。

```bash
TC_THINKING_HACK=1 python3 collect.py "你的 query"
```

## 5. 目录结构

```
cc-trajectory-collector/                 # 代码仓库
├── config.py                 # 端口/上游/模型/key/超时/RUNS_DIR/THINKING_HACK
├── proxy.js                  # 透明日志反向代理(Node 零依赖)+ thinking-hack 请求改写
├── collect.py                # 单条编排:query -> SFT json + HTML
├── collect_concurrent.py     # 并发编排:多 query 共享代理 + 按 session 分流
├── lib/
│   ├── pty_driver.py         # PTY 驱动交互式 sc claude + 完成检测 + 清启动框
│   ├── build_trajectory.py   # calls.jsonl -> 轨迹(逐响应拼接/跨压缩 + thinking 解析/剥脚手架)
│   ├── to_sft.py             # 轨迹 -> SFT 扁平格式
│   └── render_html.py        # 轨迹 -> 聊天式 HTML
└── plugins/pdf-tools/        # 自带 PDF 三件套 skill(见 §6)

~/Desktop/cc-trajectory-runs/            # 产物目录(仓库外!见 §8 防泄漏)
└── <时间戳>_<slug>/
    ├── calls.jsonl           # 代理原始抓取(含 auth token,勿外传)
    ├── workspace/            # 该 run 的隔离 cwd(agent 文件/PDF 落这里)
    ├── tmp/                  # 该 run 独立 TMPDIR
    ├── trajectory.json       # 中间态(块结构)
    ├── trajectory_sft.json   # 训练用
    ├── trajectory.html       # 可视化
    └── proxy.out
```

## 6. 启动前准备(必读)

跑之前,这些必须就位,否则会失败或能力降级:

**① 基础依赖**
- **Node.js**(跑 `proxy.js`,零三方依赖)、**Python 3**(纯标准库)。
- **`sc`(stepcode / Claude Code)已安装且已鉴权** —— 关键:先确认 `sc claude` 自己能正常跑通一句话(否则采集器也跑不起来)。

**② API key(注入内层会话)**
采集器会把 key 透传给内层 sc claude。确保以下任一环境变量已设(`config.API_KEY` 按序取):
```bash
export ANTHROPIC_AUTH_TOKEN=<你的 StepFun/模型代理 key>   # 或 ANTHROPIC_API_KEY / MODEL_PROXY_API_KEY / TC_API_KEY
```

**③ baseurl 会被临时改写 → 用完自动还原**
采集器运行时会把 `sc system baseurl` 指向本地代理,**正常/异常结束都会还原**。若进程被 `SIGKILL` 强杀未还原,手动:
```bash
sc system baseurl https://models-proxy.stepfun-inc.com
```
跑之前可先 `sc system baseurl` 确认它是真上游(不是某个 `127.0.0.1:...` 死代理)。

**④ 产物目录在 git 仓库外(默认已配好)**
`RUNS_DIR` 默认 `~/Desktop/cc-trajectory-runs`(仓库外),**别改回项目目录内**(否则 git 提交记录会泄漏进 system prompt,见 §8)。

**⑤ PDF 链路额外依赖(只做 PDF 攻略类任务才需要)**
`plugins/pdf-tools/` 已自带 `travel-guide-mobile-pdf` / `amap-mcp` / `step-search` 三个 skill(用 `--plugin-dir` 自动加载,无需手动装)。但要让 PDF 真跑通,内层机器还需(缺了只是降级,不崩):
- **Playwright + Chromium**(render PDF):`npm i -g playwright && npx playwright install chromium`(render 时 `NODE_PATH` 指向全局 node_modules)。
- **`STEPFUN_API_KEY`**(step-search 联网)、**`AMAP_MCP_KEY`**(amap 地图);env 随 `os.environ` 透传给内层 cc。

**⑥ clone**
```bash
git clone https://github.com/Chargiie/cc-trajectory-collector.git
cd cc-trajectory-collector
node -v && python3 -V && sc system baseurl     # 一键自检
```

## 6.5 怎么用

**单条采集**:
```bash
python3 collect.py "你的 query"
# 产物在 ~/Desktop/cc-trajectory-runs/<时间戳>_<slug>/:trajectory_sft.json + trajectory.html
```

**原始 CoT 模式**(thinking hack,见 §4):
```bash
TC_THINKING_HACK=1 python3 collect.py "你的 query"
```

**并发采集(单机,无 Docker)**:多 query 共享一个代理、按 session 分流;**信号量限流**,同时只跑 `--concurrency` 个,其余排队。
```bash
python3 collect_concurrent.py "query1" "query2" "query3"          # 默认并发 2
python3 collect_concurrent.py --queries-file queries.txt          # 给多少都行，仍只 2 个同时跑
python3 collect_concurrent.py --queries-file queries.txt -j 4     # 提到 4（需探测通过）
# 也支持 TC_THINKING_HACK=1 / --model / --max-seconds / --no-html
```
> **并发数 `--concurrency`/`-j`**:默认 **2**;只有机器**探测通过**(`>=8` 核且 `>=16GB`)才允许加到 **4**,硬上限 4。探测不过时即使传 `-j 4` 也会自动降到 2 并打印提示。给多少 query 都安全——信号量保证同时只跑 effective 个,不会一次性全开打爆机器。
> ⚠ 并发的各 query **必须互不相同**(代理按 query 内容认领各自的 session 文件;相同 query 无法区分)。
> 每条 run 各自独立 `workspace/` 和 `TMPDIR`,产物各落自己目录。

## 7. 配置(`config.py`)

| 项 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| 代理端口 | `TC_PROXY_PORT` | 8787 | 占用时自动顺延找空闲端口 |
| 上游地址 | `TC_UPSTREAM_URL` | `https://models-proxy.stepfun-inc.com` | 代理转发目标 |
| API key | `TC_API_KEY` | 取 `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY`/`MODEL_PROXY_API_KEY` | 注入会话;`--bare` 必需 |
| 模型 | `TC_MODEL` | `claude-opus-4-8` | sc 会映射成实际服务模型(实测落 `claude-opus-4-7`) |
| **thinking hack** | `TC_THINKING_HACK` | 0 | **1=拿原始 CoT**(见 §4) |
| 极简模式 | `TC_BARE` | 0 | 1=跳过 hooks/memory/CLAUDE.md(改变 system 形态) |
| 静默判定 | `TC_QUIET_SECONDS` | 15 | 末次调用后静默多久判定完成 |
| 单 run 上限 | `TC_MAX_SECONDS` | 0 | 硬超时秒;**0=无限**(只靠 end_turn 收尾) |
| **产物目录** | `TC_RUNS_DIR` | `~/Desktop/cc-trajectory-runs` | **必须在 git 仓库外**(见 §8) |

## 8. 防上下文污染 & 防泄漏(本版重点)

数据要"干净、可复现、贴近部署",必须消除以下污染。本版修了三类泄漏:

1. **git 提交记录泄漏(最隐蔽)**:Claude Code 的 system prompt 有 `gitStatus` 段(含**近期 commit 信息**),只要 cwd 在 git 仓库内就会注入。旧版 workspace 在 `项目/runs/` 内 → 每条轨迹的 system 都混进了**本仓库的开发提交**(且随提交变化、不可复现)。
   - **修法**:`RUNS_DIR` 默认移到仓库外(`~/Desktop/cc-trajectory-runs`)→ cwd 不在仓库 → 无 gitStatus。**附带**:含 token 的 `calls.jsonl` 也不在仓库内,杜绝误提交。
   - ⚠ 切勿把 `TC_RUNS_DIR` 设回项目目录内。
2. **hack 脚手架泄漏**:hack 注入的 guidance(system)/ suffix(user)会被代理写进请求,从而进数据。
   - **修法**:`build_trajectory` 重建时按锚点/固定串**剥除** guidance 与 suffix,最终样本的 system/user 干净。
3. **`<thinking>` 解析被嵌套假标签破坏**:模型在 thinking 里**引用含字面 `<thinking>...</thinking>` 的指令**时,旧的非贪婪正则会被内层假闭合标签提前截断,导致 thinking 抽不全、残留漏进 content。
   - **修法**:改用**深度感知(配对计数)**解析,深度归零才算真闭合;并抽取**所有最外层**块(支持一轮多个 thinking)。

**其它隔离**:
- 每 run **全新会话**,绝不 `--continue/--resume`。
- 每 run **唯一 cwd** → 空 memory 命名空间(system 不注入 MEMORY.md);run 后删除该命名空间防堆积。
- 每 run **独立 TMPDIR**(`runs/<id>/tmp`)+ 启动前清 `/tmp` 渲染残留(PDF skill 写死 `/tmp/pg-*.png`,防读到上一轮旧图)。
- 每 run 独立代理端口 + 独立 `calls.jsonl`。

## 9. 已知问题 / 限制

- **偶发 SSE 截断**:上游链路(火山 `volcalb` 等)**间歇性**掐断流式响应,末条 `stop_reason=None`、无 `message_stop`。非确定性,重跑大概率成功;无限时模式下会一直挂,需 `Ctrl-C`。
- **交互式反问会挂起**:agent 调 `AskUserQuestion` 无人应答会干等。区分:**截断**=无 `message_stop`;**反问**=响应完整且末工具是 `AskUserQuestion`。
- **超长 run 触发上下文压缩**:`build_trajectory` 已用「逐响应拼接」跨压缩还原,不丢轮。
- **单机并发已支持**(`collect_concurrent.py`):因 `sc system baseurl` 是全局值、macOS 上无法按进程设(env/假HOME/`STEPCODE_*` 全被忽略),改用「一个共享代理 + 按 `x-claude-code-session-id` 分流」;各 run 独立 workspace/TMPDIR,skill 渲染走 `$TMPDIR/pg`。**约束**:并发各 query 必须互不相同(按 query 认领 session 文件)。
- **模型差异**:复杂"排版迭代收敛"类任务上,`step-3.7-flash` 易反复横跳/收不敛;`claude-opus` 稳定。

## 10. 安全注意

- `~/Desktop/cc-trajectory-runs/`(含 `calls.jsonl`,**请求头有完整 auth token**)在仓库外,勿外传。
- 最终 `trajectory_sft.json` **不含** header/密钥。
- 运行期间临时改 `sc system baseurl`,正常结束(含异常)自动还原;若被 SIGKILL 未还原,手动:`sc system baseurl https://models-proxy.stepfun-inc.com`。
