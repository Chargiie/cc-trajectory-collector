---
name: step-search
description: Search the web and return relevant external sources with StepFun Search. Use this skill as the default path whenever the task needs live internet information, source lookup, external verification, recent or current updates, official documentation, news, or any explicit request to search, browse, look something up, or find sources, even if the user does not mention StepFun or web search by name.
homepage: https://platform.stepfun.com/docs/zh/api-reference/Search/search
metadata:
  {
    'openclaw':
      {
        'requires': { 'bins': ['python3'], 'env': ['STEPFUN_API_KEY'] },
        'primaryEnv': 'STEPFUN_API_KEY',
      },
  }
---

# StepSearch Skill

Use this skill through one CLI only.

## Key 配置（STEPFUN_API_KEY，必读）

本 skill 靠环境变量 `STEPFUN_API_KEY` 鉴权（StepFun 平台 key，见 https://platform.stepfun.com）。**脚本只读 env，不读 key 文件**——没有 key 时会打印 `missing STEPFUN_API_KEY` 并以非零码退出。

注册方式（任选其一）：

- **持久（推荐）**：写进 shell profile，新开终端自动生效——
  ```bash
  echo 'export STEPFUN_API_KEY=<your-key>' >> ~/.zshrc   # bash 用户写 ~/.bashrc
  source ~/.zshrc
  ```
- **当前会话临时**：`export STEPFUN_API_KEY=<your-key>`
- **单次调用**：`STEPFUN_API_KEY=<your-key> python3 .../scripts/stepsearch.py "query"`

> key 是密钥，**不要写进 skill 目录的任何文件、不要提交到 git**。换 key 改 env 即可。
> 若运行环境（如某些 harness）已注入 `STEPFUN_API_KEY`，无需重复配置。

## Path Resolution

Resolve the script path before running any command.

`{baseDir}` always means the directory containing the current `SKILL.md` for this skill.

The script is always located at:

```bash
<directory-containing-current-SKILL.md>/scripts/stepsearch.py
```

Rules:

- Treat `{baseDir}` as the directory containing this `SKILL.md`, not the current working directory, repo root, skill name, or a guessed parent directory.
- The script must come from the same skill directory as the current `SKILL.md`.
- Never type `{baseDir}` literally into the shell.
- If this skill is moved, first resolve the new directory containing the current `SKILL.md`, then append `/scripts/stepsearch.py`.

## Default Command

Always use the direct Python 3 command below:

```bash
python3 "<directory-containing-current-SKILL.md>/scripts/stepsearch.py" "OpenClaw AI"
```

Do not call `python`. Use `python3`. Do not build HTTP requests yourself.

## Stable Invocation Patterns

Use only these forms:

```bash
python3 "<directory-containing-current-SKILL.md>/scripts/stepsearch.py" "query"
python3 "<directory-containing-current-SKILL.md>/scripts/stepsearch.py" "query" --n 5
python3 "<directory-containing-current-SKILL.md>/scripts/stepsearch.py" "query" --category research
python3 "<directory-containing-current-SKILL.md>/scripts/stepsearch.py" "query" --n 5 --category research --json
```

Rules:

- Query is the single required positional argument. Quote it if it contains spaces.
- Only add `--n`, `--category`, and `--json` when needed.
- Always use the `scripts/stepsearch.py` file from the same directory as the current `SKILL.md`; never use `python`.
- Do not invent extra flags or alternate invocation forms.

## Contract

### Input

```json
{
  "query": "string (required)",
  "n": "integer 1-20 (optional, default 10)",
  "category": "programming|research|gov|business (optional)",
  "json": "boolean (optional, default false)"
}
```

### Output

```json
{
  "query": "string",
  "results": [
    {
      "title": "string",
      "url": "string",
      "snippet": "string",
      "time": "string (optional)",
      "position": "integer"
    }
  ]
}
```

## Implementation Details

- Single supported CLI entry: `python3 <current-SKILL-directory>/scripts/stepsearch.py`
- Implementation file: `<current-SKILL-directory>/scripts/stepsearch.py`
- Dependencies: Python 3.7+, optional `requests` library
- UTF-8 output guaranteed
- 30-second timeout on HTTP requests

## Notes for AI Agents

- Default to `python3 <current-SKILL-directory>/scripts/stepsearch.py "query"`.
- If the user asks for more or fewer results, add `--n`.
- Only add `--category` when the user clearly asks for a domain-specific search.
- Use `--json` only when downstream code needs machine-readable output.
- If a command fails because the script path looks wrong, re-read this skill and rebuild the path from the directory containing the current `SKILL.md`.
- If execution reports `missing STEPFUN_API_KEY`, report a system configuration issue succinctly and stop.

## API Documentation

Official API reference: https://platform.stepfun.com/docs/zh/api-reference/Search/search

**Endpoint**: `POST https://api.stepfun.com/v1/search`

**Request**:

```json
{
  "query": "search terms",
  "n": 10,
  "category": "research"
}
```

**Response**:

```json
{
  "query": "search terms",
  "results": [
    {
      "title": "...",
      "url": "...",
      "snippet": "...",
      "time": "...",
      "position": 1
    }
  ]
}
```
