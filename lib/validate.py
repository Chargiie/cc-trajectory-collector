"""产出即时校验:每条轨迹生成后立刻检查 7 项规则,返回问题 tag 列表(空=全绿)。"""
import json, os, re, glob


def validate_run(run_dir, sft_data=None):
    """校验一个 run 的产物,返回 list[str](问题 tag,空=通过)。

    sft_data: 已解析的 SFT 对象(单条,非数组)。若不传则从 run_dir 里读。
    run_dir: run 产物目录(含 workspace/ / sft_images/ 等)。
    """
    tags = []

    # --- 加载 SFT ---
    if sft_data is None:
        sft_files = glob.glob(os.path.join(run_dir, "*trajectory_sft.json"))
        if not sft_files:
            return ["缺trajectory_sft"]
        sft_data = json.load(open(sft_files[0]))
        if isinstance(sft_data, list):
            sft_data = sft_data[0]

    msgs = sft_data.get("messages", [])
    if not msgs:
        return ["messages为空"]

    # --- R1: 三件套 ---
    imgdir = os.path.join(run_dir, "sft_images")
    has_images = os.path.isdir(imgdir) and bool(os.listdir(imgdir))
    has_pdf = bool(glob.glob(os.path.join(run_dir, "workspace", "*.pdf")))
    if not has_pdf:
        tags.append("缺PDF")
    if not has_images:
        tags.append("缺sft_images")

    # --- R2a: 末轮正常结束 ---
    last = msgs[-1]
    if last.get("role") != "assistant" or last.get("tool_calls"):
        tags.append("末轮异常")

    # --- R2b: content/reasoning 无 <thinking> ---
    for m in msgs:
        cv = m.get("content")
        cs = cv if isinstance(cv, str) else json.dumps(cv, ensure_ascii=False) if cv else ""
        if "<thinking>" in cs or "<thinking>" in (m.get("reasoning_content") or ""):
            tags.append("含thinking")
            break

    # --- R3: reasoning 空比例 ≤ 10% ---
    asts = [m for m in msgs if m.get("role") == "assistant"]
    if asts:
        empty = sum(1 for m in asts if not (m.get("reasoning_content") or "").strip())
        ratio = empty / len(asts)
        if ratio > 0.10:
            tags.append(f"reasoning空{empty}/{len(asts)}")

    # --- R5: 无 base64 ---
    raw = json.dumps(sft_data, ensure_ascii=False)
    if ";base64," in raw:
        tags.append("含base64")

    # --- R6: 无连续 assistant ---
    for i in range(1, len(msgs)):
        if msgs[i].get("role") == "assistant" and msgs[i - 1].get("role") == "assistant":
            tags.append("连续assistant")
            break

    return tags
