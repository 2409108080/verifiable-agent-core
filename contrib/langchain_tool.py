"""LangChain 适配器：只挂一个写工具，没有改契约的工具。

    pip install langchain-core
    VAC_DELIVERY="report.md,summary.csv" python your_agent.py

交付名单是**部署方的参数**，不是模型的工具：从环境变量读，构造 Workspace 时封印，
之后只读。模型手里只有 write_file —— 它既改不了契约，也没有「清空」这一说。

`ContractViolation` 故意不接：那是安全事件，不该被当成「重试一次」的普通失败。
"""
import os

from langchain_core.tools import tool

from vac import ENV_DELIVERY, Workspace, WriteNotVerified, WriteRefused

_NAMES = [n.strip() for n in
          os.environ.get(ENV_DELIVERY, "").replace("，", ",").split(",") if n.strip()]
ws = Workspace("workspace", required_files=_NAMES or None)


@tool
def write_file(path: str, content: str) -> str:
    """把内容写入工作区文件，写完会读回逐字节校验。

    Args:
        path: 文件名，必须用任务点名的名字（如 report.md）
        content: 要写入的完整文本
    """
    try:
        r = ws.write_and_verify(path, content)
        return f"OK 已写入 {r.path}（{r.bytes} 字节，校验通过）"
    except WriteRefused as e:
        # 把拒绝原因原样交回模型：它需要知道「该改成什么名字」才能重试
        return f"FAILED 物理闸拒绝（{e.reason}）：{e.detail}"
    except WriteNotVerified as e:
        return f"FAILED 写入未通过读回校验：{e}。这东西现在不在盘上，不要声称已交付。"


# agent = create_react_agent(llm, [write_file])     ← 只有一个工具，这是故意的
