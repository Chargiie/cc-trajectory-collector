# trajectory-collector

> 来源:基于 fangfeiteng 的 trajectory-collector,修了一个 bug——当前 `sc`(Claude Code v2.1.195) 启动时 stepcode 弹的"有新版本"交互框会占住 stdin,导致 query 打不进输入框;现已在发 query 前先清掉启动框,并给 PTY 设真实窗口尺寸。详见 `lib/pty_driver.py`。

把**一个 query** 用 `sc claude`(Claude Code)真实跑一遍,自动产出一条 **SFT 训练格式**的 agent 轨迹——包含完整的 system prompt、工具 schema、明文 thinking、多轮工具调用与结果——并附带一份聊天式 HTML 可视化。

## 项目介绍

### 解决什么问题
本地 Claude Code 的会话 transcript(`~/.claude/projects/.../*.jsonl`)虽然存了对话和**明文 thinking**,但**缺少**两样训练所必需的东西:
- 当轮真正发给模型的**完整 system prompt**
- 全部**工具的 input schema**

这两样是客户端在每次 API 请求里现拼的,不落盘到 transcript。本项目在 `base_url` 外面包一层**本地日志反向代理**,把请求/响应原样 tee 下来,从而拿到 system prompt + tool schema;同时坚持**交互式(非 `-p`)**启动,保证上游下发**明文 thinking**(headless `-p` 模式抓到的 thinking 是空的)。

### 工作原理
```
sc claude(交互式, PTY 驱动)
        │  ANTHROPIC_BASE_URL 指向本地代理
        ▼
本地日志代理 proxy.js (127.0.0.1:<port>)
        │  原样转发, 同时 tee 落盘
        ▼
StepFun 上游 (models-proxy.stepfun-inc.com) → 模型后端
        │
        ▼
   calls.jsonl(完整请求+响应, SSE 自动重组)
        │  build_trajectory → to_sft → render_html
        ▼
trajectory_sft.json  +  trajectory.html
```

### 产物格式
顶层 SFT 扁平格式(对齐 `vm-trajectory-export-*` 样本):
- `tools[]`:`{name, description, parameters}`
- `messages[]`:`system / user / assistant / tool` 四种角色,`content` 均为字符串
  - `assistant`:`content` + `reasoning_content`(明文 thinking)+ `tool_calls[]` + `loss_mask`
  - `tool`:`tool_call_id` / `name` / `content` / `is_error`

## 目录结构
```
trajectory-collector/
├── README.md
├── config.py                 # 端口/上游/模型/key/超时 等可调参数
├── proxy.js                  # 透明日志反向代理(Node 零依赖)
├── collect.py                # 主编排:query -> SFT json + HTML
├── lib/
│   ├── pty_driver.py         # PTY 驱动交互式 sc claude + 完成检测
│   ├── build_trajectory.py   # calls.jsonl -> 去重合并轨迹(含明文 thinking)
│   ├── to_sft.py             # 轨迹 -> SFT 扁平格式
│   └── render_html.py        # 轨迹 -> 聊天式 HTML
└── runs/                     # 每次采集一个目录(gitignore)
    └── <时间戳>_<slug>/
        ├── calls.jsonl       # 代理原始抓取(含 auth token, 勿外传)
        ├── workspace/        # 该 run 的隔离工作目录(agent 文件操作落这里)
        ├── trajectory.json   # 中间态(块结构, 保留 thinking/tool_use/tool_result)
        ├── trajectory_sft.json
        ├── trajectory.html
        └── proxy.out         # 代理 stderr
```

## 部署

### 依赖
- **Node.js**(运行 `proxy.js`,零第三方依赖)
- **Python 3**(运行编排与转换脚本,仅用标准库)
- **`sc`(stepcode / Claude Code)** 已安装并完成鉴权,能正常 `sc claude` 跑通

### 自带 PDF skill(开箱即用)
本仓 `plugins/pdf-tools/` 打包了 PDF 生产三件套(`travel-guide-mobile-pdf` / `amap-mcp` / `step-search`),
采集器跑每条 query 时会用 `sc claude --plugin-dir plugins/pdf-tools` **自动加载**进内层 cc——无需手动装 skill。
(本仓同时是 plugin marketplace:别人想只把 skill 装进自己的 cc,见 `plugins/pdf-tools/README.md`。)

要让 PDF 这条链路**真跑通**,内层 cc 所在机器还需(否则对应能力降级,不崩):
- **Playwright + Chromium**(render PDF):`npm i -g playwright && npx playwright install chromium`;render 时 `NODE_PATH` 指向全局 node_modules(如 `~/.npm-global/lib/node_modules`)。
- **`STEPFUN_API_KEY`**(step-search 联网搜索):`export STEPFUN_API_KEY=<key>`。
- **`AMAP_MCP_KEY`** 或已注册 amap MCP(amap-mcp 地图数据):`export AMAP_MCP_KEY=<key>`。

> 这些 env 会随 `os.environ` 透传给内层 cc;在你的 shell 里 export 好即可。

### 步骤
```bash
# 1. clone 到本地
git clone https://github.com/Chargiie/cc-trajectory-collector.git
cd cc-trajectory-collector

# 2. 确认依赖就绪
node -v && python3 -V && sc system baseurl

# 3. 产一条 PDF 攻略轨迹(自动带上三个 skill)
python3 collect.py "带爸妈去颐和园半日游，做份手机看的 PDF 攻略"
# 产物在 runs/<时间戳>_<slug>/:trajectory_sft.json(训练用) + trajectory.html(看)
```

### 配置(`config.py`)
| 项 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| 代理端口 | `TC_PROXY_PORT` | 8787 | 占用时自动顺延找空闲端口 |
| 上游地址 | `TC_UPSTREAM_URL` | `https://models-proxy.stepfun-inc.com` | 代理转发目标 |
| API key | `TC_API_KEY` | 取 `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY`/`MODEL_PROXY_API_KEY` | 注入会话;`--bare` 模式必需 |
| 模型 | `TC_MODEL` | `claude-opus-4-8` | sc 会映射成实际服务模型 |
| 极简模式 | `TC_BARE` | 0 | 1=跳过 hooks/memory/CLAUDE.md(改变 system 形态) |
| 静默判定 | `TC_QUIET_SECONDS` | 15 | 末次调用后静默多久判定完成 |
| 单 run 上限 | `TC_MAX_SECONDS` | 0 | 硬超时秒数;**0=无限**(默认, 不主动截断, 只靠 end_turn 收尾) |

> key 默认沿用环境里的 token,**不必**写进文件。若硬编码到 `config.py`,注意它不在 `.gitignore`,别提交带真实 key 的版本。

## 使用

### 基本
```bash
python3 collect.py "你的 query"
```

### 带参数
```bash
python3 collect.py "带爸妈去颐和园半日游,做份PDF攻略" \
  --model claude-opus-4-8
# 默认不限时,跑到模型自然收尾(end_turn)为止
```
> 如需封顶时间用 `--max-seconds N`(秒);另有 `--no-html`、`--bare` 按需使用。
> ⚠ 无限时模式下,若遇上游流截断或 `AskUserQuestion` 反问,会**一直挂着**,需人工 `Ctrl-C` 中止。

### 运行后
- 产物在 `runs/<时间戳>_<slug>/`,核心是 **`trajectory_sft.json`**(训练用)和 **`trajectory.html`**(浏览器打开看)。
- 命令结束会打印各产物路径与统计(消息数/工具数/主loop步数/模型)。

### 它会自动做的事
- 起一个**本 run 专属**的代理(独立端口 + 独立 `calls.jsonl`)。
- 临时把 `sc system baseurl` 指向代理,**结束(含异常)自动还原**成原值。
- 在隔离的 `workspace/` 里以 `--dangerously-skip-permissions` **全自动**跑(工具不弹权限确认,副作用落在该目录)。
- 跑完删除本 run 派生的 memory 命名空间。

## 防上下文污染(软隔离)
- **每 run 全新会话**:绝不带 `--continue/--resume`。
- **每 run 唯一 cwd**(`workspace/`)→ 派生出**空的 memory 命名空间** → system prompt 不注入任何 `MEMORY.md` 内容;run 后再 `rmtree` 该命名空间防堆积。
- **preflight 告警**祖先目录 / 全局 `~/.claude/CLAUDE.md`(它每次固定加载,属固定上下文)。
- 每 run 独立代理端口 + 独立日志,互不干扰。

## 已知问题
- **偶发 SSE 截断**:上游链路(火山 `volcalb` 等)可能在单轮**间歇性**掐断流式响应,表现为末条调用 `stop_reason=None`、无 `message_stop`。**非确定性**——同一 query 重跑大概率成功。默认无限时模式下会一直挂,需人工 `Ctrl-C`。
- **交互式反问会挂起**:若 agent 调用 `AskUserQuestion`,PTY 无人应答会一直等(可通过末条是否为 `AskUserQuestion` 区分于截断)。
- 区分两类卡死:**截断**=无 `message_stop`;**反问**=响应完整且末工具是 `AskUserQuestion`。

## 安全注意
- `runs/`(含 `calls.jsonl`,**请求头里有完整 auth token**)已在 `.gitignore`,勿外传/入库。
- 最终 `trajectory_sft.json` **不含** header,无密钥。
- 运行期间会临时改 `sc system baseurl`,正常结束会还原;若被强杀(SIGKILL)未还原,手动:`sc system baseurl https://models-proxy.stepfun-inc.com`。
