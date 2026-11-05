"""Verifiable-Agent-Core —— 可验证智能体核心。

四个函数，一层物理闸：

    from vac import Workspace, judge_3state

    ws = Workspace("workspace", required_files=["report.md", "summary.csv"])
    ws.write_and_verify("report.md", text)          # 写盘 + 读回逐字节比对
    if ws.verify_all_delivered(): ...              # 验收：都在盘上且没被调包
    v = judge_3state(result)                        # 成了 / 没成 / 判不出来
    if v:                                           # unknown 一定走 else
        ...

交付契约只能由开发者在构造时传，或调 `init_task()` 设定；设完在内存里只读。
模型没有改契约的工具 —— 把「先声明啥也不用交」这条路从架构上删掉了。

`write_and_verify` 内部串了四道闸：交付契约（不写任务没点名的名字）、空内容
（没有可见字符就不算交付 —— 零宽字符也算没有）、防误删锁（不拿空壳覆盖真账）、
读回校验（不对就等于没写成）。

只有 stdlib，没有依赖，不用配 Key，import 就能用。
"""
from .gate import (
    ENV_DELIVERY,
    MAX_WRITE_BYTES,
    Contract,
    ContractViolation,
    Receipt,
    Workspace,
    WriteNotVerified,
    WriteRefused,
    default_workspace,
    guard_overwrite,
    init_task,
    write_and_verify,
)
from .verdict import (
    FAILED,
    PASSED,
    UNKNOWN,
    Verdict,
    VerdictFailed,
    judge_3state,
)

__version__ = "0.1.4"

__all__ = [
    "ENV_DELIVERY",
    "FAILED",
    "MAX_WRITE_BYTES",
    "PASSED",
    "Contract",
    "ContractViolation",
    "Receipt",
    "UNKNOWN",
    "Verdict",
    "VerdictFailed",
    "Workspace",
    "WriteNotVerified",
    "WriteRefused",
    "__version__",
    "default_workspace",
    "guard_overwrite",
    "init_task",
    "judge_3state",
    "write_and_verify",
]
