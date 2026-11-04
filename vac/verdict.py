"""vac.verdict —— 三态判据。

    judge_3state(outcome) -> Verdict   # passed / failed / unknown

为什么必须是三态：二态只有一个空档，而现实有三个 —— 做成了、没做成、**没法判断**。
把第三种塞进任何一个，都会产生假账：塞进 True 是假成功（下游拿它去规划下一步，
全盘建立在空气上）；塞进 False 是假失败（它可能明明是好的，却被记成执行者的错）。
所以 None 是一等公民，不是默认值。

而且 `if verdict:` 只有 passed 才为真 —— 顺手写 if 的代码不会把「判不出来」当成功。
这一条落在语言层，不靠调用方守规矩。

归一规则是白名单，不是黑名单：
    True / False                → passed / failed
    None                        → unknown
    1 / 0 / "ok" / "true"       → unknown   （1 == True 在 Python 里成立，是最容易
                                             被混过去的地方，所以不强转）
    {"passed"|"done"|"ok"|"success"|"verified": bool} → 按值，按此顺序取第一个真 bool
    {"unknown": True}           → unknown   （显式声明「我知道我不知道」）
    其它一切                     → unknown
认不出的归 unknown 不归 failed：判据不够是判据的问题，不是执行者的错。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PASSED", "FAILED", "UNKNOWN", "Verdict", "VerdictFailed", "judge_3state"]

PASSED, FAILED, UNKNOWN = "passed", "failed", "unknown"

# 顺序即优先级：显式的 passed 优先于笼统的 ok，
# 避免 {"passed": False, "ok": True} 被读成通过。
_KEYS = ("passed", "done", "ok", "success", "verified")


class VerdictFailed(RuntimeError):
    """require() 在 failed / unknown 时抛这个。

    让「没通过」在调用栈上有明确位置，而不是变成一句 return 被静默吞掉。
    """

    def __init__(self, verdict: Verdict):
        self.verdict = verdict
        super().__init__(f"[{verdict.state}] {verdict.reason or 'no reason given'}")


@dataclass(frozen=True)
class Verdict:
    """一次判断的结果。state 只有三种取值，构造时校验。"""

    state: str
    reason: str = ""
    raw: object = None

    def __post_init__(self) -> None:
        if self.state not in (PASSED, FAILED, UNKNOWN):
            raise ValueError(f"state 只能是 passed/failed/unknown，收到 {self.state!r}")

    @property
    def passed(self) -> bool:
        return self.state == PASSED

    @property
    def failed(self) -> bool:
        return self.state == FAILED

    @property
    def unknown(self) -> bool:
        return self.state == UNKNOWN

    def __bool__(self) -> bool:
        """只有 passed 为真。这是全库最容易被忽略的一行：它让
        `if judge_3state(x):` 在「判不出来」时走 else 分支。"""
        return self.state == PASSED

    def require(self) -> None:
        """不是 passed 就抛 VerdictFailed。给「这一步不过就不许往下走」用。"""
        if self.state != PASSED:
            raise VerdictFailed(self)

    def as_dict(self) -> dict:
        return {"state": self.state, "passed": self.passed, "reason": self.reason}


def _from_dict(d: dict) -> Verdict:
    if d.get("unknown") is True:
        return Verdict(UNKNOWN, str(d.get("reason") or "调用方标记为判不出来"), d)
    why = str(d.get("reason") or d.get("why") or "")
    for k in _KEYS:
        v = d.get(k)
        # 必须 is True / is False：1 和 0 在这里不算数。isinstance(1, bool) 为假、
        # 而 1 == True 为真 —— 两种写法只差一个字符，后果差一整本账。
        if v is True:
            return Verdict(PASSED, why, d)
        if v is False:
            return Verdict(FAILED, why, d)
    return Verdict(UNKNOWN, "返回值里没有可判的布尔字段", d)


def judge_3state(outcome, *, label: str = "", journal=None) -> Verdict:
    """把任意「执行结果」归一成三态。

    关键约定：**None 永远是 unknown，永远不会变成 passed**。
    journal 给了路径就追加一行 JSONL —— 账本是 append-only 的：
    判断会被推翻，历史不该被改写。
    """
    if isinstance(outcome, Verdict):
        v = outcome
    elif outcome is True:
        v = Verdict(PASSED, "", outcome)
    elif outcome is False:
        v = Verdict(FAILED, "", outcome)
    elif outcome is None:
        v = Verdict(UNKNOWN, "没有拿到结果", None)
    elif isinstance(outcome, dict):
        v = _from_dict(outcome)
    else:  # 1/0/"ok"/列表 —— 一律不猜
        v = Verdict(UNKNOWN, f"判不了这种返回值：{type(outcome).__name__}", outcome)

    if journal:
        _append(Path(journal), v, label)
    return v


def _append(path: Path, v: Verdict, label: str) -> None:
    """追加一行。O_APPEND 单次 write：并发写不会互相截断。"""
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "label": str(label or "")[:160],
           "state": v.state, "reason": v.reason[:300]}
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if os.name == "nt":
        flags |= os.O_BINARY  # 不加这个，"\n" 会写成 "\r\n" 两字节
    fd = os.open(str(path), flags, 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
