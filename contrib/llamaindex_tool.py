"""LlamaIndex 适配器：只有写工具，没有改契约的工具。

    pip install llama-index-core
    VAC_DELIVERY="report.md,summary.csv" python your_agent.py

名单由部署方在进程启动前给（环境变量或 `Workspace(required_files=...)`）。
工具描述里写清「名字由任务定」，模型才有机会第一次就写对；真写错了，闸负责拒绝 ——
描述提高命中率，闸保证底线。
"""
import os

from llama_index.core.tools import FunctionTool

from vac import ENV_DELIVERY, Workspace, WriteNotVerified, WriteRefused

_NAMES = [n.strip() for n in
          os.environ.get(ENV_DELIVERY, "").replace("，", ",").split(",") if n.strip()]
ws = Workspace("workspace", required_files=_NAMES or None)


def write_file(path: str, content: str) -> str:
    """把内容写入工作区文件并读回校验。文件名必须用任务点名的那个。"""
    try:
        r = ws.write_and_verify(path, content)
        return f"OK 已写入 {r.path}（{r.bytes} 字节，校验通过）"
    except WriteRefused as e:
        return f"FAILED 物理闸拒绝（{e.reason}）：{e.detail}"
    except WriteNotVerified as e:
        return f"FAILED 写入未通过读回校验：{e}。不要声称已交付。"


write_tool = FunctionTool.from_defaults(
    fn=write_file, name="write_file",
    description="写入工作区文件。path 必须是任务点名的交付文件名；写完自动校验。")

# agent = ReActAgent.from_tools([write_tool], llm=llm)
