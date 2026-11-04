"""四个函数的合同测试。全部离线，不联网、不调模型、不留垃圾。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vac import (
    MAX_WRITE_BYTES,
    UNKNOWN,
    Verdict,
    VerdictFailed,
    Workspace,
    WriteNotVerified,
    WriteRefused,
    judge_3state,
)


@pytest.fixture()
def ws(tmp_path):
    return Workspace(tmp_path / "work")


# ── 闸一：交付契约 ────────────────────────────────────────────
def test_delivery_blocks_self_invented_name(ws):
    ws = Workspace(ws.root, required_files=["report.md", "summary.csv"])
    ws.write_and_verify("report.md", "ok\n")
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("README_final.md", "内容全对但名字不对\n")
    assert ei.value.reason == "delivery"
    assert "summary.csv" in ei.value.detail
    # 被拒的时候盘上不许留东西
    assert not (ws.root / "README_final.md").exists()


def test_delivery_is_complete_only_when_all_present(ws):
    ws = Workspace(ws.root, required_files=["report.md", "summary.csv"])
    ws.write_and_verify("report.md", "a\n")
    assert ws.delivery_state()["missing"] == ["summary.csv"]
    ws.write_and_verify("summary.csv", "b\n")
    assert ws.delivery_state()["complete"] is True
    # 交齐不等于解封：任务没结束，写清单外的名字照样拦
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("notes.md", "随便写\n")
    assert ei.value.reason == "delivery"
    # 真正的开关是部署方的 end_task()
    snap = ws.end_task()
    assert snap["complete"] is True
    ws.write_and_verify("notes.md", "任务结束之后才自由\n")


def test_delivery_matches_basename_not_path(ws):
    ws = Workspace(ws.root, required_files=["report.md"])
    r = ws.write_and_verify("deep/nested/report.md", "x\n")
    assert r.delivery_complete is True


def test_no_delivery_means_no_gate(ws):
    """开发者不传 required_files = 明确不做交付约束，写什么名字都行。"""
    assert ws.contract is None
    ws.write_and_verify("whatever.md", "x\n")


def test_delivery_state_reads_disk_not_memory(ws):
    """P0 回归：文件写了又被删掉，delivery_state() 曾经仍报 complete: True。

    内存里的 `_delivered` 记的是「我写过」，不是「盘上还在」。两套判据
    （delivery_state 看内存 / verify_all_delivered 看盘）一旦分家，
    假绿灯就长在缝里：大屏说交齐了，验收说没交。
    """
    ws = Workspace(ws.root, required_files=["report.md", "summary.csv"])
    ws.write_and_verify("report.md", "报告\n")
    ws.write_and_verify("summary.csv", "日期,数量\n")
    assert ws.delivery_state()["complete"] is True

    # 交齐之后把一份删掉：盘上没有了，就不许再报 complete
    (ws.root / "report.md").unlink()
    st = ws.delivery_state()
    assert st["complete"] is False
    assert st["missing"] == ["report.md"]
    assert ws.verify_all_delivered() is False
    assert st["complete"] == ws.verify_all_delivered()      # 同一个判据，不是两套

    # 盘上有、但内容被换过 → 是「对不上」，不是「齐了」
    ws.write_and_verify("report.md", "报告 v2\n")
    (ws.root / "report.md").write_text("被换过的内容\n", encoding="utf-8")
    st = ws.delivery_state()
    assert st["complete"] is False
    assert st["missing"] == [] and st["unverified"] == ["report.md"]
    assert ws.verify_all_delivered() is False

    # 回执上那一格同源：删掉一份再写另一份，回执不许盖「交付完成」的章
    ws.write_and_verify("report.md", "报告 v3\n")
    (ws.root / "summary.csv").unlink()
    r = ws.write_and_verify("report.md", "报告 v4\n")
    assert r.verified is True and r.delivery_complete is False


# ── 闸二：防误删锁 ────────────────────────────────────────────
REAL_CSV = "rank,topic,hot\n" + "".join(f"{i},话题{i},{(10 - i) * 137}\n" for i in range(1, 8))


def test_overwrite_guard_blocks_shell(ws):
    ws.write_and_verify("hot.csv", REAL_CSV)
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("hot.csv", "rank,topic,hot\n")
    assert ei.value.reason == "overwrite"
    # 旧数据必须原样还在
    assert (ws.root / "hot.csv").read_text(encoding="utf-8") == REAL_CSV


def test_overwrite_guard_allows_update(ws):
    ws.write_and_verify("hot.csv", REAL_CSV)
    bigger = REAL_CSV + "8,新话题,999\n"
    ws.write_and_verify("hot.csv", bigger)
    assert (ws.root / "hot.csv").read_text(encoding="utf-8") == bigger


def test_overwrite_guard_ignores_small_files(ws):
    """3 行的旧文件不算「真账」，允许整体替换 —— 否则会把正常改写锁死。"""
    ws.write_and_verify("notes.md", "a\nb\nc\n")
    ws.write_and_verify("notes.md", "x\n")


def test_overwrite_guard_can_be_used_standalone(ws):
    (ws.root).mkdir(parents=True, exist_ok=True)
    (ws.root / "hot.csv").write_text(REAL_CSV, encoding="utf-8")
    with pytest.raises(WriteRefused):
        ws.guard_overwrite("hot.csv", "空壳\n")
    ws.guard_overwrite("hot.csv", REAL_CSV + "多一行\n")  # 不抛就是过了


def test_locks_off_reproduces_bare_agent(ws):
    """关掉锁 = 同一个 Agent 去掉这一层。

    注意它**不是干净的对照组**：空内容检查与锁无关，这里照样拒；
    能对照的只有「防误删 + 交付契约 + 下限」这三样一起下线。
    """
    bare = Workspace(ws.root, locks=False, required_files=["report.md"])
    bare.write_and_verify("hot.csv", REAL_CSV)
    bare.write_and_verify("hot.csv", "空壳\n")          # 防误删锁不拦
    bare.write_and_verify("invented.md", "x\n")         # 交付契约不拦
    assert (ws.root / "hot.csv").read_text(encoding="utf-8") == "空壳\n"
    # 契约下限也跟着下线：A/B 里的裸 Agent 不该被另一条规定单独锁住
    loose = Workspace(ws.root / "b", locks=False, required_files=["r.md"], min_bytes=100)
    loose.write_and_verify("r.md", "短\n")
    assert loose.delivery_state()["complete"] is True


def test_allow_shrink_turns_off_the_guard(ws):
    """逃生口：开发者明确知道「这个文件就是要改短」时，可以主动关掉防误删锁。

    默认 False —— 默认必须是拦。这个开关只对构造它的人开放，模型碰不到。
    """
    ws.write_and_verify("hot.csv", REAL_CSV)
    with pytest.raises(WriteRefused):                     # 默认：拦
        ws.write_and_verify("hot.csv", "rank,topic,hot\n")

    loose = Workspace(ws.root, allow_shrink=True)
    loose.write_and_verify("hot.csv", "rank,topic,hot\n")  # 开了：放行
    assert (ws.root / "hot.csv").read_text(encoding="utf-8") == "rank,topic,hot\n"


def test_allow_shrink_does_not_disable_empty_or_delivery(ws):
    """逃生口只关「防误删」这一道，别的闸照样立着 —— 别把它当万能钥匙。"""
    shrinkable = Workspace(ws.root, required_files=["report.md"], allow_shrink=True)
    with pytest.raises(WriteRefused) as ei:               # 空内容仍然拒
        shrinkable.write_and_verify("report.md", "\u200b")
    assert ei.value.reason == "empty"
    with pytest.raises(WriteRefused) as ei:               # 交付契约仍然拦
        shrinkable.write_and_verify("invented.md", "x\n")
    assert ei.value.reason == "delivery"


# ── 闸三：原子写 + 读回 ───────────────────────────────────────
def test_write_roundtrip_is_byte_exact(ws):
    """末尾换行必须一模一样。production 上这里曾经吃掉过一个 \\n。

    空串和纯空白不在这里 —— 它们现在被 `empty` 闸无条件拒绝，
    见 test_empty_or_blank_content_is_refused。
    """
    for text in ("甲\n乙\n", "甲\n乙", "没有换行的一行", "  前后有空格但中间有内容  \n"):
        r = ws.write_and_verify("t.txt", text)
        assert (ws.root / "t.txt").read_bytes() == text.encode("utf-8")
        assert r.bytes == len(text.encode("utf-8"))
    assert (ws.root / "t.txt").read_text(encoding="utf-8") == "  前后有空格但中间有内容  \n"


def test_empty_or_blank_content_is_refused(ws):
    """P0 回归：`write_and_verify("report.md", "")` 曾经返回 verified=True。

    空文件和「写好了」在返回值里长得一模一样，这是本库最该拦住的一种假成功。
    拒它跟有没有契约无关 —— 是写入层的规矩。
    """
    for blank in ("", " ", "\n", " \n\t\r\n", "　\n"):     # 最后一个只有一个全角空格
        with pytest.raises(WriteRefused) as ei:
            ws.write_and_verify("report.md", blank)
        assert ei.value.reason == "empty"
    assert not (ws.root / "report.md").exists()            # 一个字节都没落
    assert ws.contract is None                              # 没设清单也一样拒

    # 契约下限 min_bytes / min_lines：占位文件不算交
    strict = Workspace(ws.root, required_files=["report.md"], min_bytes=20, min_lines=3)
    with pytest.raises(WriteRefused) as ei:
        strict.write_and_verify("report.md", "太短\n")      # 7 字节 / 1 行
    assert ei.value.reason == "too_small" and "20 字节" in ei.value.detail
    with pytest.raises(WriteRefused) as ei:
        strict.write_and_verify("report.md", "第一行够长够长\n第二行\n")   # 够字节、不够行数
    assert ei.value.reason == "too_small" and "3 行" in ei.value.detail
    assert strict.delivery_state()["missing"] == ["report.md"]   # 被拒的写入不许记账

    ok = strict.write_and_verify("report.md", "# 本周复盘\n\n总作品 25 件。\n")
    assert ok.bytes >= 20 and ok.lines >= 3
    assert strict.verify_all_delivered() is True

    # 负数不是「不设限」，是打错字：直接报，别静默当 0
    with pytest.raises(ValueError):
        Workspace(ws.root, required_files=["r.md"], min_bytes=-1)


def test_zero_width_only_content_is_refused(ws):
    """P0 回归（0.1.2）：只有零宽字符的「隐形文件」也是空文件。

    `strip()` 只认空白，`"\\u200b\\u200d\\ufeff".strip()` 是非空字符串 —— 落盘之后
    编辑器里看不见、diff 里看不见、`wc -c` 有 3 个字节，验收照样记账。这是
    0.1.1 漏掉的那个洞：判据看的是「有没有被 strip 掉」，而不是「有没有可见字符」。
    """
    # 零宽空格、ZWJ、BOM、方向控制符、全角空格、组合符（没有基字符时什么都不显示）
    for invisible in ("\u200b", "\u200b\u200d\ufeff", "\u200e\u200f",
                      "\u3000\u3000", "\u0301\u0301", "\ufeff\n\u200b"):
        with pytest.raises(WriteRefused) as ei:
            ws.write_and_verify("report.md", invisible)
        assert ei.value.reason == "empty"
        assert "可见字符" in ei.value.detail
    assert not (ws.root / "report.md").exists()      # 一个字节都没落


def test_zero_width_around_real_text_is_allowed(ws):
    """反过来不能误伤：正文里夹着零宽字符（复制粘贴常见）必须照常放行。"""
    text = "\ufeff# 本周复盘\u200b\n\n总作品 25 件。\n"
    r = ws.write_and_verify("report.md", text)
    assert (ws.root / "report.md").read_bytes() == text.encode("utf-8")
    assert r.bytes == len(text.encode("utf-8"))
    # 标点也算可见字符：它是「写得少」，该由契约下限管，不该被 empty 拦
    ws.write_and_verify("punct.md", "。")


def test_delivery_error_comes_before_empty_error(ws):
    """错名字 + 空内容，先报名字 —— 一次调用只暴露一个问题，省一轮 Token。

    反过来的话模型会先挨「内容是空的」，补完内容再来一次才发现名字也错，
    同一件事花两轮对话。
    """
    ws = Workspace(ws.root, required_files=["report.md"])
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("README_final.md", "")
    assert ei.value.reason == "delivery"
    assert "report.md" in ei.value.detail
    # 名字对、内容空 → 这时才轮到 empty
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("report.md", "")
    assert ei.value.reason == "empty"


def test_write_refuses_to_escape_root(ws):
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("../outside.md", "x")
    assert ei.value.reason == "escape"


def _make_dir_link(target: Path, link: Path) -> None:
    """建一个指向目录的链接。POSIX 用 symlink；Windows 没权限就退到 junction。

    两条路都不通就抛 OSError，让用例 skip —— 「没权限建链接」不是被测代码的问题。
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def test_symlink_escape_is_refused(ws, tmp_path):
    """工作区里一个指向区外的链接，不能变成后门。

    `../` 好挡，链接不好挡：路径字面上还在工作区内，跟着链接走过去就是别人的盘。
    所以 resolve() 要顺着链接看真实位置，而不是只做字符串规整。
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("区外的原件", encoding="utf-8")
    ws.root.mkdir(parents=True, exist_ok=True)
    link = ws.root / "escape"

    try:
        _make_dir_link(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("这个平台不允许建链接（Windows 需开发者模式或管理员）")

    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("escape/secret.md", "被覆盖了")
    assert ei.value.reason == "escape"
    assert (outside / "secret.md").read_text(encoding="utf-8") == "区外的原件"


def test_append(ws):
    ws.write_and_verify("log.csv", "a\n")
    r = ws.write_and_verify("log.csv", "b\n", append=True)
    assert r.appended is True
    assert (ws.root / "log.csv").read_text(encoding="utf-8") == "a\nb\n"

    # 8MB 上限按「写完之后文件多大」算。0.1.0 里先判片段、再拼旧文件，
    # 于是「先写 6MB、再 append 4MB」会拿到一张 10MB 的回执 —— 上限形同虚设。
    ws.write_and_verify("big.txt", "x" * (MAX_WRITE_BYTES - 1))
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("big.txt", "y" * 1024, append=True)
    assert ei.value.reason == "too_large"
    assert (ws.root / "big.txt").stat().st_size == MAX_WRITE_BYTES - 1   # 拒了就没动
    ws.write_and_verify("big.txt", "y", append=True)                     # 不超限照常
    assert (ws.root / "big.txt").stat().st_size == MAX_WRITE_BYTES


def test_unverified_is_not_success(ws, monkeypatch):
    """读回来不一致 → 抛异常，绝不返回一个 ok=True 的回执。"""
    dirty = "被别的东西覆写了".encode()
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda self: dirty)
    with pytest.raises(WriteNotVerified):
        ws.write_and_verify("x.md", "我写的内容")


# ── 三态 ────────────────────────────────────────────────────
@pytest.mark.parametrize("outcome,state", [
    (True, "passed"),
    (False, "failed"),
    (None, UNKNOWN),
    (1, UNKNOWN),          # 1 == True 但 isinstance(1, bool) 是假
    (0, UNKNOWN),
    ("ok", UNKNOWN),
    ({}, UNKNOWN),
    ({"ok": True}, "passed"),
    ({"ok": 1}, UNKNOWN),  # 最容易混过去的一格
    ({"done": False}, "failed"),
    ({"verified": True}, "passed"),
    ({"passed": False, "ok": True}, "failed"),  # 显式字段优先
    ({"unknown": True}, UNKNOWN),
])
def test_judge_3state(outcome, state):
    assert judge_3state(outcome).state == state


def test_none_never_passes_in_boolean_context():
    v = judge_3state(None)
    assert v is not None
    assert bool(v) is False          # if 语句不会把「判不出来」当成功
    assert v.passed is False
    assert v.unknown is True


def test_require_raises_on_unknown_and_failed():
    for bad in (None, False, {"ok": 1}):
        with pytest.raises(VerdictFailed):
            judge_3state(bad).require()
    judge_3state(True).require()     # 不抛就是过了


def test_bad_state_rejected():
    with pytest.raises(ValueError):
        Verdict("maybe")


def test_journal_is_append_only(tmp_path):
    j = tmp_path / "journal.jsonl"
    judge_3state(True, label="第一轮", journal=j)
    judge_3state(None, label="第二轮", journal=j)
    rows = [json.loads(x) for x in j.read_text(encoding="utf-8").splitlines()]
    assert [r["state"] for r in rows] == ["passed", UNKNOWN]
    assert rows[1]["label"] == "第二轮"
