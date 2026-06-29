# trajectory-collector 配置
import os

# 代理
PROXY_PORT = int(os.environ.get("TC_PROXY_PORT", "8787"))
UPSTREAM_URL = os.environ.get("TC_UPSTREAM_URL", "https://models-proxy.stepfun-inc.com")

# StepFun / Anthropic API key
# 用途:--bare 极简模式下 Claude Code 只认 ANTHROPIC_API_KEY(不读 OAuth/keychain);
#      普通模式 sc 用自己的鉴权,这里留空也能跑。
# 默认沿用环境里的 ANTHROPIC_AUTH_TOKEN;要硬编码可直接把 "" 改成你的 key。
# 注意:写在这里=明文密钥,config.py 不在 .gitignore,别提交带真实 key 的版本。
API_KEY = (
    os.environ.get("TC_API_KEY")
    or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    or os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("MODEL_PROXY_API_KEY")
)

# sc claude
MODEL = os.environ.get("TC_MODEL", "claude-opus-4-8")  # sc 会映射成实际服务模型
USE_BARE = os.environ.get("TC_BARE", "0") == "1"        # 1=极简(跳过 hooks/memory/CLAUDE.md),改变 system 形态

# content-thinking hack:1=代理改写主 loop 请求(关原生 thinking,让模型把 <thinking> 当正文输出),
# 从而拿到原始完整 CoT 而非摘要。代价:会改 system+注入 suffix,且 thinking 移到正文里。默认关。
THINKING_HACK = os.environ.get("TC_THINKING_HACK", "0") == "1"

# 完成检测
QUIET_SECONDS = int(os.environ.get("TC_QUIET_SECONDS", "15"))   # 末次调用后静默多久判定结束
MAX_SECONDS = int(os.environ.get("TC_MAX_SECONDS", "0"))        # 单 run 硬上限;0=无限(不主动截断),只靠 end_turn 自然收尾
DISMISS_DELAY = float(os.environ.get("TC_DISMISS_DELAY", "3"))  # 启动后多久发一个回车,清掉 stepcode 的"有新版本"等启动交互选择框(否则它吃掉 stdin,query 进不去输入框)
READY_DELAY = float(os.environ.get("TC_READY_DELAY", "5"))      # 清完启动框后再等多久(界面就绪)才发 query

# 主 loop 调用判定(过滤后台/404/424/小辅助)
MAIN_MIN_TOOLS = 10

# 路径
HOME = os.path.expanduser("~")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
# runs 必须在 git 仓库外:否则 sc claude 的 cwd 在仓库内,Claude Code 会把仓库的 gitStatus
# (含近期提交信息)嵌进 system prompt,污染每条轨迹且不可复现。可用 TC_RUNS_DIR 覆盖。
# 默认放桌面上一个与项目平级的目录(桌面本身不是 git 仓库;切勿放进项目目录内)。
RUNS_DIR = os.environ.get("TC_RUNS_DIR") or os.path.join(HOME, "Desktop", "cc-trajectory-runs")
PROXY_JS = os.path.join(PROJECT_DIR, "proxy.js")

# 自带的 plugin 目录(打包了 travel-guide-mobile-pdf / amap-mcp / step-search 三个 skill);
# 内层 sc claude 用 --plugin-dir 加载它,从而自动带上这三个 skill。设为空字符串可禁用。
PLUGIN_DIR = os.environ.get("TC_PLUGIN_DIR", os.path.join(PROJECT_DIR, "plugins", "pdf-tools"))
