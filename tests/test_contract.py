"""契约不可变 —— 针对「模型自己把清单清空」这一类攻击的回归测试。

七条对应审查提的七个场景，另加一条环境变量篡改（它是最容易被忽略的一条旁路）。
全部离线，不联网、不调模型。
"""
from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from vac import (
    Contract,
    ContractViolation,
    Workspace,
    WriteRefused,
)

ROOT = Path(__file__).resolve().parents[1]


# 1. 正常设置清单，合法文件允许写
def test_contract_allows_listed_files(tmp_path):
    ws = Workspace(tmp_path / "w", required_files=["report.md", "summary.csv"])
    assert ws.contract_names == ("report.md", "summary.csv")
    r = ws.write_and_verify("report.md", "# 报告\n总额 25\n")
    assert r.verified is True
    assert r.delivery_complete is False          # 还欠 summary.csv
    r2 = ws.write_and_verify("summary.csv", "日期,数量\n")
    assert r2.delivery_complete is True
    assert ws.verify_all_delivered() is True


# 2. 模型尝试调 set_delivery("") —— 这个方法不存在，任何适配器也不许挂出来
def test_set_delivery_is_gone_from_library(tmp_path):
    ws = Workspace(tmp_path / "w", required_files=["report.md"])
    assert not hasattr(ws, "set_delivery")
    with pytest.raises(AttributeError):
        ws.set_delivery([])  # type: ignore[attr-defined]
    # 库级别也没有这个函数
    import vac
    assert not hasattr(vac, "set_delivery")


def test_no_adapter_exposes_contract_writer():
    """所有适配器（含 contrib/ 里的参考实现）不许出现能改契约的函数。"""
    banned = {"set_delivery", "init_task", "end_task", "clear_delivery"}
    found = {}
    for f in sorted(list((ROOT / "adapters").glob("*.py"))
                    + list((ROOT / "contrib").glob("*.py"))):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        defs = {n.name for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        hit = defs & banned
        if hit:
            found[f.name] = sorted(hit)
    assert not found, f"适配器暴露了改契约的入口：{found}"


def test_adapters_have_no_direct_write_side_door():
    """适配器源码里不许有绕过闸的落盘 API：open(..., 'w') / write_text / shutil。"""
    banned = (".write_text(", ".write_bytes(", "shutil.copy", "os.rename", "open(")
    hits = {}
    for f in sorted(list((ROOT / "adapters").glob("*.py"))
                    + list((ROOT / "contrib").glob("*.py"))):
        src = f.read_text(encoding="utf-8")
        bad = [b for b in banned if b in src]
        if bad:
            hits[f.name] = bad
    assert not hits, f"适配器里有绕过 write_and_verify 的写入口：{hits}"


def test_openai_tool_schema_has_only_write_file():
    """function-calling 的 schema 是模型看到的全部世界 —— 里面只有 write_file。"""
    sys.path.insert(0, str(ROOT))
    try:
        from adapters import openai_function as m
    finally:
        sys.path.remove(str(ROOT))
    assert [t["function"]["name"] for t in m.TOOLS] == ["write_file"]
    assert m.dispatch("set_delivery", {"names": ""}).startswith("FAILED 没有这个工具")


# 3. 写清单外的名字 → 拒绝，盘上不许留东西
def test_unlisted_file_is_refused(tmp_path):
    ws = Workspace(tmp_path / "w", required_files=["report.md"])
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("README_final.md", "内容很对，名字不对\n")
    assert ei.value.reason == "delivery"
    assert not (ws.root / "README_final.md").exists()


# 4. 任务执行中途重置契约 → 拒绝（含传空清单），契约原样不动
def test_mid_task_reset_is_refused(tmp_path):
    ws = Workspace(tmp_path / "w", required_files=["report.md"])
    ws.write_and_verify("report.md", "第一批\n")
    for attempt in (["other.md"], ["a.md"]):
        with pytest.raises(ContractViolation):
            ws.init_task(attempt)
    assert ws.contract_names == ("report.md",)   # 没被改宽、也没被清空


def test_empty_required_files_is_refused(tmp_path):
    """空清单 = 「什么都不用交」= 任何交付都算过。这条必须堵死在初始化。"""
    for empty in ([], [""], ["  ", ""], "", ()):
        with pytest.raises(ContractViolation):
            Workspace(tmp_path / "w", required_files=empty)

    # init_task 这条入口同样拒绝，且不在实例上留下半封印状态
    ws = Workspace(tmp_path / "w2")
    with pytest.raises(ContractViolation):
        ws.init_task([])
    assert ws.contract is None

    # 「明确不做交付约束」是另一个开关，仍然允许，但验收必须是 False
    loose = Workspace(tmp_path / "w3", required_files=None)
    assert loose.verify_all_delivered() is False
    # 传单个字符串不会被逐字符拆开（否则会封印出一个没人看得懂的契约）
    one = Workspace(tmp_path / "w4", required_files="report.md")
    assert one.contract_names == ("report.md",)


def test_env_rewrite_after_seal_is_refused(tmp_path, monkeypatch):
    """封印后重写 VAC_DELIVERY（清空 / 加名字 / 删掉）一律抛异常。"""
    monkeypatch.delenv("VAC_DELIVERY", raising=False)
    ws = Workspace(tmp_path / "w", required_files=["report.md"])
    ws.write_and_verify("report.md", "x\n")
    for value in ("", "anything.md", "report.md,extra.md"):
        monkeypatch.setenv("VAC_DELIVERY", value)
        with pytest.raises(ContractViolation):
            ws.write_and_verify("report.md", "y\n")
    monkeypatch.delenv("VAC_DELIVERY", raising=False)
    assert ws.write_and_verify("report.md", "z\n").verified is True


# 5. 未授权修改契约，不影响最终验收
def test_tamper_cannot_flip_acceptance(tmp_path, monkeypatch):
    ws = Workspace(tmp_path / "w", required_files=["report.md", "summary.csv"])
    ws.write_and_verify("report.md", "真报告\n")

    # 三种改法全失败：重设 / 原地改元组 / 改冻结对象
    with pytest.raises(ContractViolation):
        ws.init_task([])
    with pytest.raises(AttributeError):
        ws.contract_names.append("什么都不用交.md")  # type: ignore[attr-defined]
    with pytest.raises((FrozenInstanceError, AttributeError)):
        ws.contract.names = ()                    # type: ignore[misc]
    monkeypatch.setenv("VAC_DELIVERY", "")
    with pytest.raises(ContractViolation):
        ws.verify_all_delivered()

    # 篡改一律失败之后，验收仍按原契约走：缺 summary.csv 就是过不了
    monkeypatch.delenv("VAC_DELIVERY", raising=False)
    assert ws.verify_all_delivered() is False
    ws.write_and_verify("summary.csv", "a,b\n")
    assert ws.verify_all_delivered() is True

    # 交付后再偷改文件内容 —— 也不认
    (ws.root / "report.md").write_text("被换过的内容\n", encoding="utf-8")
    assert ws.verify_all_delivered() is False


def test_no_contract_is_not_a_pass(tmp_path):
    """没设清单 = 无从验收。运维忘了配，不能被一个 True 盖过去。"""
    ws = Workspace(tmp_path / "w")
    ws.write_and_verify("x.md", "x\n")
    assert ws.verify_all_delivered() is False


# 6. 开发者用专用方法初始化新任务 → 成功（构造参数与 init_task 两条路都验）
def test_developer_can_init_task(tmp_path):
    ws = Workspace(tmp_path / "w")
    assert ws.contract is None
    c = ws.init_task(["note.md"])
    assert isinstance(c, Contract)
    assert ws.contract is c
    assert ws.write_and_verify("note.md", "正文\n").delivery_complete is True
    assert ws.verify_all_delivered() is True


# 7. 新任务契约不污染上一任务的契约
def test_new_task_does_not_pollute_old_contract(tmp_path):
    ws = Workspace(tmp_path / "w")
    old = ws.init_task(["a.md"])
    ws.write_and_verify("a.md", "旧任务的内容\n")
    assert ws.verify_all_delivered() is True

    ws.end_task()
    new = ws.init_task(["b.md"])

    assert old is not new
    assert old.names == ("a.md",)          # 旧契约对象一字未变
    assert ws.contract_names == ("b.md",)
    # 旧任务的名字在新任务里不再合法
    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("a.md", "混进来了\n")
    assert ei.value.reason == "delivery"
    assert ws.verify_all_delivered() is False
    ws.write_and_verify("b.md", "新任务的内容\n")
    assert ws.verify_all_delivered() is True


# 附：manifest 只能从开发者通道进，环境变量只读一次
def test_env_channel_builds_contract_at_import_time(monkeypatch):
    monkeypatch.setenv("VAC_DELIVERY", "report.md, summary.csv")
    names = [n.strip() for n in os.environ["VAC_DELIVERY"].split(",") if n.strip()]
    ws = Workspace("/tmp/vac-env-check", required_files=names)
    assert ws.contract_names == ("report.md", "summary.csv")
    assert ws.contract.env == "report.md, summary.csv"
    monkeypatch.setenv("VAC_DELIVERY", "report.md")
    with pytest.raises(ContractViolation):
        ws.delivery_state()
    assert os.environ["VAC_DELIVERY"] == "report.md"   # 确实改了，但闸不认


# 适配器也要能读下限：VAC_MIN_BYTES / VAC_MIN_LINES
_ADAPTER_PROBE = """
from adapters.openai_function import _MIN_BYTES, _MIN_LINES, ws
c = ws.contract
print("ENV", _MIN_BYTES, _MIN_LINES)
print("CONTRACT", ws.contract_names, c.min_bytes if c else -1, c.min_lines if c else -1)
try:
    ws.write_and_verify("report.md", "{content}")
    print("WROTE")
except Exception as e:
    print("REFUSED", type(e).__name__, getattr(e, "reason", ""))
"""


def _probe_adapter(tmp_path, env_extra: dict) -> subprocess.CompletedProcess:
    """在干净的子进程里 import 适配器 —— 模块一导入就封印契约，只能这么测。

    子进程会往 stdout/stderr 写中文，所以这里**显式规定 UTF-8**，不等宿主机
    locale：Windows 的默认控制台代码页（en-US 是 cp1252）编不出中文，子进程会在
    print 那一行抛 UnicodeEncodeError、返回码变 1 —— 于是一条「适配器读不读
    环境变量」的测试，会因为在英文版 Windows 上跑而红。判据不能依赖宿主机的
    代码页，那跟被测行为一点关系都没有。
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("VAC_")}
    env.update(env_extra)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"      # 子进程的 stdout/stderr 一律 UTF-8
    return subprocess.run(
        [sys.executable, "-c", _ADAPTER_PROBE.format(content="短\\n")],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def test_adapter_reads_min_limits_from_env(tmp_path):
    """设了 VAC_MIN_BYTES / VAC_MIN_LINES，适配器里就真的生效。

    以前这两个值只能在代码里写死 —— 部署方要么改源码，要么没有下限。
    现在走环境变量，和 VAC_DELIVERY 同一条通道（都在 import 时封印）。
    """
    out = _probe_adapter(tmp_path, {"VAC_DELIVERY": "report.md",
                                    "VAC_MIN_BYTES": "100", "VAC_MIN_LINES": "3"})
    assert out.returncode == 0, out.stderr
    assert "ENV 100 3" in out.stdout
    assert "CONTRACT ('report.md',) 100 3" in out.stdout
    assert "REFUSED WriteRefused too_small" in out.stdout     # 7 字节 1 行，不够


def test_adapter_rejects_non_integer_limits(tmp_path):
    """打错字就报错，不静默当 0 —— 否则「设了下限」是一句没人验证的假设。"""
    out = _probe_adapter(tmp_path, {"VAC_DELIVERY": "report.md",
                                    "VAC_MIN_BYTES": "200B"})
    assert out.returncode != 0
    assert "VAC_MIN_BYTES" in out.stderr and "200B" in out.stderr


def test_adapter_warns_when_limits_have_no_contract(tmp_path):
    """没有清单还设下限 = 静默失效。喊一声，别让人以为它在起作用。"""
    out = _probe_adapter(tmp_path, {"VAC_MIN_BYTES": "100"})
    assert out.returncode == 0, out.stderr
    assert "ENV 100 0" in out.stdout                    # 读到了，但没有契约可挂
    assert "CONTRACT () -1 -1" in out.stdout
    assert "不会起作用" in out.stderr


# 回执不许出现假绿灯：没有契约时「交付齐了」是不可能的
def test_receipt_without_contract_is_not_complete(tmp_path):
    """contract is None 时，Receipt.delivery_complete 必须是 False。

    旧实现算的是 `not [n for n in () if ...]` —— 空清单恒等于「齐了」，
    于是一次普通写入拿到的回执上盖着「交付完成」的章。假绿灯长在自己家门上。
    """
    ws = Workspace(tmp_path / "w")          # 没传 required_files = 没有契约
    assert ws.contract is None
    r = ws.write_and_verify("anything.md", "内容\n")
    assert r.verified is True               # 写是真的写成了
    assert r.delivery_complete is False     # 但「交付齐了」是假的，不许为真
    assert r.as_dict()["delivery_complete"] is False
    # 同一个判据在 delivery_state 里也要一致，否则两处会各说一套
    assert ws.delivery_state()["complete"] is False
    assert ws.verify_all_delivered() is False


def test_receipt_completes_only_when_contract_fully_delivered(tmp_path):
    """有契约时，回执的 delivery_complete 严格跟着「还缺不缺」走。"""
    ws = Workspace(tmp_path / "w", required_files=["report.md", "summary.csv"])
    r1 = ws.write_and_verify("report.md", "报告\n")
    assert r1.delivery_complete is False     # 还差 summary.csv
    r2 = ws.write_and_verify("summary.csv", "a,b\n")
    assert r2.delivery_complete is True      # 交齐的那一次才为真
    assert ws.delivery_state()["complete"] is True


# 交齐 ≠ 解封：放开权限的只有一个动作，而且在部署方手里
def test_complete_does_not_open_the_gate(tmp_path):
    """清单交齐之后，写清单外的名字仍要拦。

    旧实现是 `if missing and base not in names` —— missing 一空，整个闸就不看了。
    代价是「交完四份作业 = 拿到万能钥匙」，之后写什么都能过、也不会有回执上的异常。
    """
    ws = Workspace(tmp_path / "w", required_files=["report.md"])
    ws.write_and_verify("report.md", "报告\n")
    assert ws.verify_all_delivered() is True          # 该交的确实交齐了

    with pytest.raises(WriteRefused) as ei:
        ws.write_and_verify("scratch.md", "偷偷加一份\n")
    assert ei.value.reason == "delivery"
    assert "还没结束" in ei.value.detail               # 拒绝理由要能读懂
    assert not (tmp_path / "w" / "scratch.md").exists()  # 一个字节都没落

    # 深度路径也一样，不能靠换个名字绕过
    with pytest.raises(WriteRefused):
        ws.write_and_verify("deep/scratch.md", "x\n")


def test_end_task_is_the_only_way_to_open_the_gate(tmp_path):
    """end_task() 之后才放开；它返回的验收快照是任务的最终结论。"""
    ws = Workspace(tmp_path / "w", required_files=["report.md"])
    ws.write_and_verify("report.md", "报告\n")
    with pytest.raises(WriteRefused):
        ws.write_and_verify("after.md", "x\n")

    snap = ws.end_task()
    assert snap["complete"] is True
    assert snap["missing"] == []
    assert ws.contract is None

    # 任务已结束：可以做收尾动作（写日志、清临时文件）
    ws.write_and_verify("after.md", "任务之后的内容\n")
    assert (tmp_path / "w" / "after.md").exists()
    # 但此时没有契约可验，验收必须用 end_task() 的返回值，不能指望这里给 True
    assert ws.verify_all_delivered() is False


def _manifest_check(root: Path) -> int:
    """跑一遍仓库自带的清单校验器，返回退出码。用子进程，模拟使用者怎么用它。

    同 `_probe_adapter`：校验器会打印中文，这里显式 UTF-8，不靠宿主机的代码页。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_manifest.py"), str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env).returncode


# P0：CRLF 检出导致清单整份失配
def test_manifest_survives_crlf_checkout(tmp_path):
    """0.1.0 的清单哈希是按 Windows 上的 CRLF 字节算的 —— 换台机器检出就全红。

    两道保险，缺一不可：
      1. `.gitattributes` 让检出即 LF（`* text=auto eol=lf`），清单自己标 `-text`；
      2. `tools/check_manifest.py` 把 CRLF 归一后再比 —— 换行不是内容。
    """
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in attrs
    assert "MANIFEST.sha256 -text" in attrs

    rows = [ln for ln in (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    assert rows, "MANIFEST.sha256 是空的"

    # 仓库里的字节本身必须是 LF，否则在 Linux 上算出来的哈希和清单对不上
    for row in rows:
        digest, name = row.split("  ", 1)
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" not in raw, f"{name} 在仓库里带 CRLF"
        assert hashlib.sha256(raw).hexdigest() == digest, f"{name} 与清单不一致"

    # 模拟 Windows 检出：整棵树转成 CRLF，校验器仍须通过
    tree = tmp_path / "crlf-tree"
    for row in rows:
        name = row.split("  ", 1)[1]
        dst = tree / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((ROOT / name).read_bytes().replace(b"\n", b"\r\n"))
    (tree / "MANIFEST.sha256").write_bytes((ROOT / "MANIFEST.sha256").read_bytes())
    assert _manifest_check(tree) == 0

    # 改一个字节就该红 —— 归一不等于不校验
    victim = tree / rows[0].split("  ", 1)[1]
    victim.write_bytes(victim.read_bytes() + b"x")
    assert _manifest_check(tree) == 1

