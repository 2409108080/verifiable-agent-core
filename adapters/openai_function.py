"""OpenAI / 任何 function-calling 接口的适配器（不依赖 SDK，纯 JSON schema）。

    pip install openai   # 只有跑下面那段 loop 才需要

任何按 OpenAI 协议传 tools 的模型都能用：DeepSeek、Qwen、Moonshot 都一样。
**只暴露 write_file 一个工具**：清单从 `VAC_DELIVERY` 读，下限从 `VAC_MIN_BYTES` /
`VAC_MIN_LINES` 读，都在构造时封印，模型无权改动。

    VAC_DELIVERY="report.md,summary.csv" VAC_MIN_BYTES=200 VAC_MIN_LINES=5 python your_agent.py
"""
import json
import os
import sys

from vac import ENV_DELIVERY, Workspace, WriteNotVerified, WriteRefused, judge_3state

ENV_MIN_BYTES = "VAC_MIN_BYTES"    # 每份契约文件至少多少字节
ENV_MIN_LINES = "VAC_MIN_LINES"    # 每份契约文件至少多少行


def _env_int(name: str) -> int:
    """读一个非负整数环境变量。没设或空串 = 0（不额外要求）。

    打错字（`VAC_MIN_BYTES=200B`）直接抛，不静默当 0 —— 否则「我设了下限」就变成
    一句没人验证过的假设，测试环境里永远发现不了。
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是整数（字节数 / 行数），收到 {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name} 不能为负：{value}")
    return value


_NAMES = [n.strip() for n in
          os.environ.get(ENV_DELIVERY, "").replace("，", ",").split(",") if n.strip()]
_MIN_BYTES = _env_int(ENV_MIN_BYTES)
_MIN_LINES = _env_int(ENV_MIN_LINES)
if (_MIN_BYTES or _MIN_LINES) and not _NAMES:
    # 下限只对契约内的文件生效。没有清单还设下限 = 静默失效，不如喊一声。
    print(f"警告：设了 {ENV_MIN_BYTES}/{ENV_MIN_LINES}，但 {ENV_DELIVERY} 是空的。"
          "契约下限只约束点名的文件，这两项不会起作用。", file=sys.stderr)
ws = Workspace("workspace", required_files=_NAMES or None,
               min_bytes=_MIN_BYTES, min_lines=_MIN_LINES)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "写入工作区文件并读回校验。path 必须是任务点名的文件名。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件名，如 report.md"},
            "content": {"type": "string", "description": "完整文本内容"}},
            "required": ["path", "content"]},
    },
}]


def dispatch(name: str, args: dict) -> str:
    """名字 → 函数。返回值一律是字符串，直接塞回 role=tool 的消息。"""
    if name != "write_file":
        return f"FAILED 没有这个工具：{name}"
    try:
        r = ws.write_and_verify(args["path"], args["content"])
        return f"OK 已写入 {r.path}（{r.bytes} 字节，校验通过）"
    except WriteRefused as e:
        return f"FAILED 物理闸拒绝（{e.reason}）：{e.detail}"
    except WriteNotVerified as e:
        return f"FAILED 未通过读回校验：{e}。不要声称已交付。"


def run(client, model: str, task: str, max_turns: int = 12) -> dict:
    """最小 Agent loop：模型要写，就走闸；收尾按盘上交付验收判三态。"""
    msgs = [{"role": "user", "content": task}]
    for _ in range(max_turns):
        resp = client.chat.completions.create(model=model, messages=msgs, tools=TOOLS)
        msg = resp.choices[0].message
        msgs.append(msg)
        if not msg.tool_calls:
            break
        for c in msg.tool_calls:
            out = dispatch(c.function.name, json.loads(c.function.arguments))
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": out})
    if ws.contract is None:
        # 没设清单 = 无从验收。判不出来就是判不出来，不许当成通过
        return judge_3state(None, label="no-contract").as_dict()
    st = ws.delivery_state()
    return judge_3state(
        {"passed": ws.verify_all_delivered(),
         "reason": f"缺：{'、'.join(st['missing']) or '无'}"}
    ).as_dict()
