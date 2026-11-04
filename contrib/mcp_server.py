"""MCP 服务器：把物理闸暴露给任何支持 MCP 的客户端（Claude Desktop / IDE / 自研）。

    pip install mcp
    VAC_DELIVERY="report.md,summary.csv" python adapters/mcp_server.py

两个工具：写文件 / 查欠账（只读）。**没有登记名单的工具** —— 名单是部署方的启动参数，
在进程启动、构造 Workspace 时封印；跑起来之后连本机调用方也改不了。

写工具没有「跳过校验」的选项：MCP 这一层不该提供一个比库里更弱的写入口。
"""
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 就地 `python adapters/mcp_server.py` 时 sys.path[0] 是 adapters/，
# 补一行父目录，免得非装包用户卡在 ModuleNotFoundError。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vac import ENV_DELIVERY, Workspace, WriteNotVerified, WriteRefused  # noqa: E402

_NAMES = [n.strip() for n in
          os.environ.get(ENV_DELIVERY, "").replace("，", ",").split(",") if n.strip()]
ws = Workspace("workspace", required_files=_NAMES or None)

mcp = FastMCP("verifiable-workspace")


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """写入工作区文件并读回逐字节校验。path 必须是任务点名的交付文件名。"""
    try:
        r = ws.write_and_verify(path, content)
        return f"OK 已写入 {r.path}（{r.bytes} 字节，校验通过）"
    except WriteRefused as e:
        return f"FAILED 物理闸拒绝（{e.reason}）：{e.detail}"
    except WriteNotVerified as e:
        return f"FAILED 未通过读回校验：{e}"


@mcp.tool()
def delivery_state() -> dict:
    """还欠哪几个交付文件（只读，不能改清单）。"""
    return ws.delivery_state()


if __name__ == "__main__":
    mcp.run()
