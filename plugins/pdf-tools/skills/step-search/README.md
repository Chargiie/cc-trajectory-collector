# stepsearch

基于 StepFun Search API 的网页搜索 skill。给定一个查询，返回相关的外部来源（标题、URL、摘要等）。本质是对官方搜索接口的薄封装，无智能体逻辑、无第三方强依赖。

## 文件结构

```
SKILL.md              # skill 描述（SKILL.md frontmatter 格式，CC / OpenClaw 通用）
scripts/stepsearch.py # CLI 实现，Python 3.7+，标准库即可（有 requests 则用，否则 urllib）
```

## 前置条件

- Python 3.7+
- 环境变量 `STEPFUN_API_KEY`：在 [platform.stepfun.com](https://platform.stepfun.com) 控制台 → API Keys 生成，需账号已开通 Search 能力。

```bash
export STEPFUN_API_KEY=<your-key>
```

可选：`STEPFUN_SEARCH_BASE_URL` 覆盖接口地址（默认 `https://api.stepfun.com/v1/search`）。

## 用法

```bash
# 基本查询
python3 scripts/stepsearch.py "你的查询"

# 指定返回条数（1-20，默认 10）
python3 scripts/stepsearch.py "Rust WASM" --n 5

# 限定类别（programming | research | gov | business）
python3 scripts/stepsearch.py "government report" --category gov

# 机器可读的原始 JSON 输出
python3 scripts/stepsearch.py "最新AI进展" --n 5 --category research --json
```

参数：

| 参数 | 说明 |
|---|---|
| `query`（位置参数，必填） | 搜索词，含空格请加引号 |
| `--n` / `-n` | 返回条数，1-20，默认 10 |
| `--category` | `programming` / `research` / `gov` / `business` |
| `--json` | 输出原始 JSON（供下游程序解析） |

## 接口契约

**输入**

```json
{
  "query": "string (required)",
  "n": "integer 1-20 (optional, default 10)",
  "category": "programming|research|gov|business (optional)",
  "json": "boolean (optional, default false)"
}
```

**输出**

```json
{
  "query": "string",
  "results": [
    { "title": "string", "url": "string", "snippet": "string", "time": "string (optional)", "position": 1 }
  ]
}
```

底层请求：`POST https://api.stepfun.com/v1/search`，header `Authorization: Bearer $STEPFUN_API_KEY`，30s 超时。

## 作为 skill 安装

把本仓库的 `SKILL.md` 与 `scripts/` 放进 skill 目录即可被 Claude Code / OpenClaw 识别：

```bash
# Claude Code
mkdir -p ~/.claude/skills/step-search
cp SKILL.md ~/.claude/skills/step-search/
cp -r scripts ~/.claude/skills/step-search/
```

之后把 `STEPFUN_API_KEY` 写入 shell 配置（如 `~/.zshrc`）持久化，即可在会话中直接触发搜索。

> 备注：`SKILL.md` frontmatter 里的 `metadata.openclaw` 块是 OpenClaw 专用，Claude Code 会忽略，留着无害。
