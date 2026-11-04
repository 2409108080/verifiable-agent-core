"""AutoGen 适配器（v0.4+）：只有一个写工具。

    pip install autogen-core
    VAC_DELIVERY="report.md,summary.csv" python your_agent.py

名单由部署方在启动前给，构造 Workspace 时封印。模型没有改契约的工具。
"""
import os

from autogen_core.tools import FunctionTool

from vac import ENV_DELIVERY, Workspace, WriteNotVerified, WriteRefused

_NAMES = [n.strip() for n in
          os.environ.get(ENV_DELIVERY, "").replace("，", ",").split(",") if n.strip()]
ws = Workspace("workspace", required_files=_NAMES or None)


def write_file(path: str, content: str) -> str:
    """写入工作区文件并读回校验。path 必须是任务点名的交付文件名。"""
    try:
        r = ws.write_and_verify(path, content)
        return f"OK 已写入 {r.path}（{r.bytes} 字节，校验通过）"
    except WriteRefused as e:
        return f"FAILED 物理闸拒绝（{e.reason}）：{e.detail}"
    except WriteNotVerified as e:
        return f"FAILED 未通过读回校验：{e}。不要声称已交付。"


write_tool = FunctionTool(
    write_file, description="写入工作区文件并读回校验。path 必须是任务点名的文件名。")

# await AssistantAgent(name="writer", model_client=client, tools=[write_tool])
