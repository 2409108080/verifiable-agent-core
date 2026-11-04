"""vac.gate —— 交付物理闸。

    ws = Workspace("workspace", required_files=["report.md", "summary.csv"])
    ws.write_and_verify("report.md", text)     原子写 + 读回逐字节比对
    ws.verify_all_delivered()                  验收：点名的都在盘上、且没被调包

交付契约**只能由开发者**在实例化时传入，或在任务开始时调 `init_task()` 设定。
设定之后契约在内存里是只读的：没有 `set_delivery`，没有 `clear`，没有「传空字符串」，
连属性都是 tuple —— 模型没有任何一条路可以把它改宽或改空。

钥匙为什么不交给被锁的人：模型只要能改契约，「不交齐不许自创名字」就变成
「先声明啥也不用交，再随便写」。那不是锁坏了，是把锁装在了门的同一侧。

四条约束，都来自实测：
1. 闸必须在写入路径上。绕过 write_and_verify 的写入不受保护，
   所以适配器只暴露这一个写入口，不暴露 open()/write_text()。
2. 关掉闸不影响写入本身。`Workspace(locks=False)` = 「没有这层锁的同一个 Agent」。
   但注意它**不再是干净的对照组**（0.1.2 起）：空内容检查与允许写入的名单无关，
   `locks=False` 也照样拒绝空文件；契约下限也跟着 `locks` 一起下线。所以它降级成
   「没有防误删锁、没有交付约束的同一个 Agent」，A/B 要用它，就得把两边的差别说全。
3. 闸自己出故障时放行 —— 锁的 bug 不能反过来把人锁在门外。
4. 空文件不是交付。空字符串、纯空白、**以及零宽字符组成的「看不见的内容」**无条件拒绝：
   一个 0 字节的 report.md 让「我写好了」和「我建了个空文件」变成同一句话，
   这是本库最该拦住、也最容易漏掉的一种假成功。
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Contract", "ContractViolation", "ENV_DELIVERY", "Receipt", "Workspace",
           "WriteNotVerified", "WriteRefused", "default_workspace", "guard_overwrite",
           "init_task", "write_and_verify"]

# 防误删锁的阈值：旧文件 ≥4 行，且**行数**或**字节数**塌掉一半以上，两条任一成立就拦。
#
# 它防的是手抖（把真账覆盖成一行表头），不是恶意的渐进式覆盖 —— 每轮删 10%，
# 每轮都不到一半，这条锁永远不响。要防那个，得靠版本控制或 append-only 日志，
# 不是这里。把话说明白，比假装它防得住更安全。
#
# 为什么不是「新内容 ≤2 行」：绝对阈值可以用填充绕过 —— 把 11 行的真账换成 1 行表头，
# 再补两行长注释凑字节数，原来的「字节砍半」就不成立了，而 3 行也大于 2 行。
# 改成新旧比例之后，填充必须填到旧账的一半行数才不触发，那时它已经不叫「空壳」了。
#
# 为什么是「或」而不是「且」：两条同时成立才拦，等于要求攻击者在行和字节两边都露馅；
# 只塌一边（长注释填字节、或一行超长 JSON 撑字节）照样是把账擦掉了。代价是
# 「11 行 CSV 原地改写成 1 行 JSON」会被拦 —— 这种改写请另存新名，别在原地做。
MIN_OLD_LINES = 4
LINE_SHRINK_RATIO = 2.0   # 新的行数 × 2 < 旧的 才算塌
SHRINK_RATIO = 2.0        # 新的字节 × 2 < 旧的 才算塌
MAX_WRITE_BYTES = 8 * 1024 * 1024  # 按「这次写完之后文件多大」算，append 也一样

ENV_DELIVERY = "VAC_DELIVERY"      # 开发者通道：进程启动前由部署方设定，不是模型工具


class WriteRefused(PermissionError):
    """写之前就被拒了，盘上什么都没发生。

    reason 是机器可判的短码：delivery（没交齐就去写别的名字）/ overwrite（空壳盖真账）
    / escape（跑出工作区）/ too_large（写完会超过单次上限）/ empty（没有可见字符的内容）
    / too_small（低于契约要求的 min_bytes / min_lines）。调用方可以按 reason 分开处理。
    """

    def __init__(self, reason: str, path: str, detail: str):
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"[{reason}] {path}: {detail}")


class WriteNotVerified(IOError):
    """写了，但读回来对不上 —— 等于没成。它把「调用没报错」和「东西真在盘上」分开：
    前者是接口的礼貌，后者才是证据。"""

    def __init__(self, path: str, expected: bytes, got: bytes):
        self.path = path
        self.expected = expected
        self.got = got
        super().__init__(f"[unverified] {path}: 应为 {len(expected)} 字节，"
                         f"盘上读到 {len(got)} 字节")


class ContractViolation(RuntimeError):
    """契约被越权改动。

    两种来源：已封印时再设一次（模型试图重置），或封印后环境变量被改（试图注入）。
    这不是「参数不合法」，是**权限**问题 —— 所以单独一个异常类型，调用方别吞。
    """


@dataclass(frozen=True, slots=True)
class Contract:
    """一次任务的交付契约。frozen + slots + tuple：设完不可改、也加不了字段。

    min_bytes / min_lines 是「交上来的东西至少得像份东西」的下限，跟着契约一起封印。
    默认 0 = 不额外要求（空内容仍然被无条件拒绝，那是写入层的规矩，不归契约管）。
    """

    names: tuple[str, ...]
    env: str | None        # 封印瞬间的 VAC_DELIVERY 快照，用来发现「事后改环境变量」
    sealed_ns: int
    min_bytes: int = 0
    min_lines: int = 0

    def __len__(self) -> int:
        return len(self.names)

    def __contains__(self, name: object) -> bool:
        return name in self.names


@dataclass(frozen=True)
class Receipt:
    """一次成功写入的回执。verified=True 是构造时就保证的，不靠调用方自觉。"""

    path: str
    abspath: Path
    bytes: int
    lines: int
    sha256: str
    existed: bool
    overwritten: bool
    appended: bool
    delivery_complete: bool   # 没有契约时恒为 False：没设清单就没有「交齐」可言

    def as_dict(self) -> dict:
        """给日志 / 工具返回值用。不含 abspath，免得本机路径漏进上下文。"""
        return {"path": self.path, "bytes": self.bytes, "lines": self.lines,
                "sha256": self.sha256, "existed": self.existed,
                "overwritten": self.overwritten, "appended": self.appended,
                "delivery_complete": self.delivery_complete, "verified": True}

    @property
    def verified(self) -> bool:
        """恒为 True —— 读回对不上根本走不到构造这一步。留着是为了读起来像一句话。"""
        return True


def _count_lines(raw: bytes) -> int:
    """字节 → 行数。末尾没换行也算一行，空文件 0 行。

    不用 splitlines()：它吃掉末尾换行，"甲\\n乙\\n" 会数成 2 行。同一个数在两处
    用不同算法，迟早在别处咬人。
    """
    if not raw:
        return 0
    return raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)


# 不可见字符的 Unicode 类别。判断「有没有可见内容」时逐字符看类别，而不是看 strip()：
#   Cc 控制符 / Cf 格式符（零宽空格 U+200B、ZWJ U+200D、BOM U+FEFF、方向控制符）
#   Cs 代理项 / Co 私用区 / Cn 未分配 / Zs 空格分隔符（含全角空格 U+3000）
#   Zl 行分隔符 / Zp 段分隔符 / Mn 非间距组合符 / Me 包围组合符
# 只有组合符没有基字符时屏幕上什么都看不见，所以 Mn/Me 也算不可见。
# 反过来：标点、符号、数字都算可见 —— 一个「。」虽然没信息量，但它确实是看得见的字符；
# 「写得少」是契约下限（min_bytes/min_lines）的活，不是这一条判据的活。
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn",
                                   "Zs", "Zl", "Zp", "Mn", "Me"})


def _has_visible(text: str) -> bool:
    """有没有至少一个看得见的字符。

    `"\\u200b".strip()` 不空 —— 零宽空格不是 ASCII 空白，strip() 拿它没辙，
    于是一份「只有零宽字符」的 report.md 能骗过按空白判断的检查，在编辑器里
    打开是空文件，在任何 diff 里也显示为空。这是 0.1.1 漏掉的那个洞。
    """
    return any(unicodedata.category(ch) not in _INVISIBLE_CATEGORIES for ch in text)


def _basename(rel) -> str:
    """report.md / work/report.md / work\\report.md 都归一到 report.md。

    交什么名字由任务定，不由目录结构定。
    """
    return str(rel).replace("\\", "/").rstrip("/").split("/")[-1]


def _normalize(names: Iterable[str] | None) -> tuple[str, ...]:
    """去空、去重、归一成 basename，保持给定顺序。

    传单个字符串（required_files="report.md"）按一个名字处理 —— 否则会被逐字符拆开，
    得到一个没人看得懂、却已经封印的契约。
    """
    if isinstance(names, str):
        names = [names]
    out: list[str] = []
    for n in names or ():
        base = _basename(n)
        if base.strip() and base not in out:
            out.append(base)
    return tuple(out)


def _limit(value: int, label: str) -> int:
    """下限必须是非负整数。传 -1 不是「不设限」，是打错字 —— 直接报，别静默当 0。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} 必须是整数，收到 {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{label} 不能为负：{value}")
    return value


@dataclass
class Workspace:
    """一个工作区 = 一个根目录 + 一份（可选的）交付契约 + 一个锁开关。

    契约在构造时给：`Workspace(root, required_files=["report.md"])`。
    之后它只读；要换任务，开发者调 `end_task()` 再 `init_task()`，模型两个都调不到。
    多 Agent 并发时一个任务一个实例，不要共用：契约是**每次任务**的状态。
    """

    root: Path | str = "workspace"
    locks: bool = True
    required_files: Iterable[str] | None = None
    min_bytes: int = 0     # 契约文件「至少多少字节」，0 = 不额外要求
    min_lines: int = 0     # 契约文件「至少多少行」，0 = 不额外要求
    # 逃生口：True = 关掉防误删锁（允许把大文件改写成小文件）。默认 False。
    # 这不是给模型用的开关，是给开发者的：只有部署方能构造 Workspace。
    # 用在「确实要原地重写成更短的形式」的地方（11 行 CSV → 1 行 JSON）；
    # 关掉之后垃圾覆盖照样会发生 —— 那道闸防的本来就是手抖，不是恶意。
    allow_shrink: bool = False
    _contract: Contract | None = field(default=None, init=False, repr=False)
    # 交付验收只认「盘上的字节」：base → (相对路径, sha256)
    _delivered: dict[str, tuple[str, str]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        _limit(self.min_bytes, "min_bytes")
        _limit(self.min_lines, "min_lines")
        if self.required_files is not None:
            self._seal(self.required_files, min_bytes=self.min_bytes, min_lines=self.min_lines)
        self.required_files = self.contract_names

    # ── 契约：开发者设定，设完封印 ──────────────────────────
    @property
    def contract(self) -> Contract | None:
        """当前契约（只读对象）。没有契约返回 None。"""
        return self._contract

    @property
    def contract_names(self) -> tuple[str, ...]:
        """交付清单的只读视图。tuple，append 会直接 AttributeError。"""
        return self._contract.names if self._contract else ()

    def _seal(self, names: Iterable[str], *, min_bytes: int = 0,
              min_lines: int = 0) -> Contract:
        norm = _normalize(names)
        if not norm:
            raise ContractViolation(
                "交付清单为空：required_files=[] 等于「什么都不用交」，"
                "任何交付都会被验收通过 —— 这正是本库要防的那种假成功。"
                "要明确不做交付约束，请传 required_files=None；要验收，就至少点名一个文件。")
        c = Contract(names=norm, env=os.environ.get(ENV_DELIVERY),
                     sealed_ns=time.monotonic_ns(),
                     min_bytes=_limit(min_bytes, "min_bytes"),
                     min_lines=_limit(min_lines, "min_lines"))
        self._contract = c
        self._delivered.clear()
        return c

    def _check_env(self) -> None:
        """封印之后环境变量被改过 → 抛。这是「重写环境变量篡改契约」的拦截点。

        只比对，不读值：改宽（加名字）、改窄（清空）、清掉，三种都算篡改。
        """
        if self._contract is None:
            return
        now = os.environ.get(ENV_DELIVERY)
        if now != self._contract.env:
            raise ContractViolation(
                f"交付契约封印后 {ENV_DELIVERY} 被改动（原为 {self._contract.env!r}，"
                f"现为 {now!r}）。运行中修改契约一律拒绝 —— 换任务请由开发者调用 end_task()。")

    def init_task(self, required_files: Iterable[str], *, min_bytes: int = 0,
                  min_lines: int = 0) -> Contract:
        """开发者专用：开一个新任务并封印契约。**不暴露给任何 Agent 工具。**

        一个实例只能封印一次；任务没结束就想重设（无论谁调）一律 ContractViolation。
        传空清单同样拒绝 —— 「什么都不用交」不是一种任务，是闸被拆了。
        换任务的正确姿势是 `end_task()` 之后再 `init_task()`。
        """
        self._check_env()
        if not _normalize(required_files):
            raise ContractViolation(
                "交付清单为空：init_task([]) 等于「什么都不用交」。"
                "不做交付约束请用 Workspace(required_files=None)；要验收就至少点名一个文件。")
        if self._contract is not None:
            raise ContractViolation(
                f"本任务的交付契约已封印：{'、'.join(self._contract.names) or '（空）'}。"
                "任务未结束前不准重设 —— 包括模型自己、包括传空清单。")
        return self._seal(required_files, min_bytes=min_bytes, min_lines=min_lines)

    def end_task(self) -> dict:
        """开发者专用：结束当前任务，解除封印并返回验收快照。模型调不到。

        这是「交齐之后能不能写别的」的唯一开关。没调它，闸一直只认清单内的名字；
        调了它，本实例不再有契约，写什么都自由 —— 但**验收要在这个调用之前做**，
        或者直接用这里返回的快照（end_task 之后再调 verify_all_delivered() 是 False，
        因为那时已经没有契约可验了）。
        """
        state = self.delivery_state()
        self._contract = None
        self._delivered.clear()
        return state

    @staticmethod
    def _check_not_blank(text, shown: str) -> None:
        """没有可见字符就无条件拒绝 —— 跟有没有契约、锁开没开都没关系。

        判据不是 `strip()`，是 `unicodedata.category()`：`strip()` 只认空白，
        「\\u200b\\u200d\\ufeff」（零宽空格 + 连接符 + BOM）能整份通过，落盘后在
        编辑器里、在 diff 里都是一个空文件 —— 但它拿到了 verified=True 的回执。
        全角空格（U+3000）同样是 `strip()` 的漏网之鱼。
        """
        content = str(text if text is not None else "")
        if not _has_visible(content):
            raise WriteRefused(
                "empty", shown,
                f"内容里没有任何可见字符（共 {len(content)} 个字符，全是空白/零宽/控制符）。"
                "空文件不是交付 —— 它会让「我写好了」和「我建了个空文件」变成同一句话。"
                "至少要写进一行看得见的内容。")

    def _minimum_hint(self) -> str:
        """把契约下限说成人话：「至少 100 字节、3 行」。"""
        c = self._contract
        if c is None:
            return "的内容"
        parts = []
        if c.min_bytes:
            parts.append(f"至少 {c.min_bytes} 字节")
        if c.min_lines:
            parts.append(f"至少 {c.min_lines} 行")
        return "的内容" + ("（" + "、".join(parts) + "）" if parts else "")

    def _minimum_gap(self, raw: bytes, base: str) -> str | None:
        """契约下限（min_bytes / min_lines）差多少。满足返回 None。

        只对**契约里的**文件生效；`locks=False` 时整条下线（那是 A/B 里的裸 Agent，
        连交付契约都不拦，没道理单独留着一条下限）。
        写的时候拦一次，验收的时候按盘上的字节再拦一次 —— 两次用同一个函数。
        """
        c = self._contract
        if c is None or not self.locks or base not in c.names:
            return None
        if c.min_bytes and len(raw) < c.min_bytes:
            return f"至少要 {c.min_bytes} 字节，实际只有 {len(raw)} 字节"
        if c.min_lines and _count_lines(raw) < c.min_lines:
            return f"至少要 {c.min_lines} 行，实际只有 {_count_lines(raw)} 行"
        return None

    def _audit(self) -> dict:
        """**唯一**的交付判据：逐条回盘上读，不信内存里那句「我写过了」。

        每个点名的文件落在且只落在一格里：
          written     盘上在、sha256 与写入时一致、且满足 min_bytes/min_lines
          unverified  盘上在，但内容被换过 / 短于下限
          missing     盘上根本没有（没交，或者交了又被删了）

        `delivery_state()` 和 `verify_all_delivered()` 都调它。以前这两处各算一套，
        盘上文件被删掉之后内存里的 `_delivered` 还在，于是大屏写「complete: True」、
        验收写「False」—— 假绿灯就长在两套判据的缝里。
        """
        self._check_env()
        required = list(self.contract_names)
        written: list[str] = []
        missing: list[str] = []
        unverified: list[str] = []
        for name in required:
            rec = self._delivered.get(name)
            if not rec:
                missing.append(name)
                continue
            shown, sha = rec
            try:
                raw = self.resolve(shown).read_bytes()
            except (OSError, WriteRefused):
                missing.append(name)      # 写完之后被删了 / 挪走了
                continue
            gap = self._minimum_gap(raw, name)
            if hashlib.sha256(raw).hexdigest() != sha or gap:
                unverified.append(name)
                continue
            written.append(name)
        return {
            "required": required,
            "written": sorted(written),
            "missing": missing,
            "unverified": unverified,
            # 没有契约 = 无从验收，恒 False。两处共用一个 `complete`，不许各算一套。
            "complete": self._contract is not None and not missing and not unverified,
        }

    def delivery_state(self) -> dict:
        """欠账摆出来：还缺哪几个、哪几个对不上。给「回喂给模型」用，别只在日志里躺着。

        `complete` 与 `verify_all_delivered()` 是同一个值（同一个 `_audit()`），
        且都来自**盘上的字节**，不是内存里的写入记录。
        """
        return self._audit()

    def verify_all_delivered(self) -> bool:
        """验收：每个点名的文件都在盘上、没被调包、且满足契约下限。

        不信内存里的「我写过了」，只认盘上的字节 —— 模型改不了契约，但能改文件；
        这里把「交付了」和「交对了」一起判。

        **没有契约时返回 False**：没设清单就无从验收，不能默认放行。运维忘了配
        `required_files` 的那种情况，正是最需要被看见的，不该被一个 True 盖过去。
        """
        return self._audit()["complete"]

    def _check_delivery(self, base: str) -> None:
        """闸一：任务没结束，就只认清单里的名字 —— **交齐了也一样**。

        早期版本是「缺文件时拦，交齐就全放行」。那等于交完四份作业就拿到一把
        万能钥匙：之后写什么都不会被打回，多写的空壳文件也没人看得见。
        现在放开权限只由一个动作触发 —— 部署方调 `end_task()`。模型手里没有它。
        """
        if not self.locks or self._contract is None:
            return
        names = self._contract.names
        if base in names:
            return
        # 欠哪几个要按盘上的真实情况说：内存里记着「写过」但文件已被删掉时，
        # 照内存报「都交齐了」会让模型去猜一个根本不存在的欠账。
        state = self._audit()
        outstanding = state["missing"] + state["unverified"]
        if outstanding:
            raise WriteRefused(
                "delivery", base,
                f"任务点名的交付文件还没交齐：{'、'.join(outstanding)}。"
                f"要求的文件名是 {outstanding[0]}，不准自创「{base}」。"
                "先把点名的交齐，再写别的。")
        raise WriteRefused(
            "delivery", base,
            f"清单上的文件都交齐了，但任务还没结束（未调用 end_task()），"
            f"这里仍然只认清单内的名字，不准写「{base}」。"
            "要写别的，先由部署方结束当前任务。")

    # ── 路径 ──────────────────────────────────────────────
    def resolve(self, rel) -> Path:
        """相对路径 → 工作区内的绝对路径。跑出工作区一律拒绝。

        用 resolve() 而不是 normpath()：符号链接也要顺着看，否则一个指向区外的
        链接就把闸绕开了。
        """
        raw = Path(str(rel))
        target = raw if raw.is_absolute() else self.root / raw
        root = self.root.resolve()
        try:
            target = target.resolve()
            target.relative_to(root)
        except ValueError:
            raise WriteRefused("escape", str(rel), f"目标不在工作区 {root} 之内") from None
        return target

    def show(self, path: Path) -> str:
        """绝对路径 → 给人看的相对路径（正斜杠，跨平台一致）。"""
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    # ── 闸二：防误删锁 ────────────────────────────────────
    def guard_overwrite(self, rel, text: str) -> None:
        """旧文件有真实数据、这次写的是空壳 → 抛 WriteRefused。

        `allow_shrink=True` 时整条跳过：那是开发者主动声明「这个文件我确实要改短」。
        过闸就静默返回：「通过了」不是一条要处理的结果。任何内部异常都放行。
        """
        path = self.resolve(rel)
        if not self.locks or self.allow_shrink or not path.is_file():
            return
        try:
            old = path.read_bytes()
            if not old:
                return
            old_lines = _count_lines(old)
            new = str(text if text is not None else "").encode("utf-8")
            new_lines = _count_lines(new)
        except Exception:  # 锁自己算不清就放行
            return
        if old_lines >= MIN_OLD_LINES:
            why = self._collapse(old_lines, len(old), new_lines, len(new))
            if why:
                raise WriteRefused(
                    "overwrite", self.show(path),
                    f"这里存着 {old_lines} 行内容（{len(old)} 字节），"
                    f"你这次只写了 {new_lines} 行（{len(new)} 字节）。{why}"
                    "没有新数据就把旧账擦掉是自毁 —— 不许。"
                    "要更新就带上新数据整个重写，或者换个名字另存。")

    @staticmethod
    def _collapse(old_lines: int, old_bytes: int, new_lines: int, new_bytes: int) -> str:
        """新旧塌陷判据：行数或字节任一塌掉一半以上 → 返回一句人话，否则空串。

        两条各自独立、任一成立就拦。原因见文件顶部常量处的注释：AND 起来会被
        「补两行长注释凑字节」绕过，那正是 0.1.0 里真实存在的洞。
        """
        if new_lines * LINE_SHRINK_RATIO < old_lines:
            return f"行数从 {old_lines} 掉到 {new_lines}（不到一半）。"
        if new_bytes * SHRINK_RATIO < old_bytes:
            return f"字节从 {old_bytes} 掉到 {new_bytes}（不到一半）。"
        return ""

    # ── 闸三：原子写 + 读回 ───────────────────────────────
    def write_and_verify(self, rel, text: str, *, append: bool = False) -> Receipt:
        """写盘，然后**读回来逐字节比对**。不一致抛 WriteNotVerified。

        顺序不能改：先过闸一（名字）、再查空内容、再过防误删锁，最后才碰盘。任何一道
        拒了，盘上一个字节都没动 —— 「被拒绝」和「写坏了」是两种事故，不能混。
        名字排在内容前面，是为了让一次调用只暴露一个问题（省掉一轮对话）。

        空内容无条件拒绝（与有没有契约无关）：一个 0 字节的 report.md 会让
        「我写好了」和「我建了个空文件」变成同一句话。这是 0.1.0 最致命的那个洞 ——
        `write_and_verify("report.md", "")` 曾经返回一个 verified=True 的回执。
        0.1.2 起判据从 `strip()` 换成 Unicode 类别：只有零宽字符/全角空格的「隐形文件」
        同样拒。

        大小上限按**这次写完之后文件多大**算：append 不是绕过 8MB 的后门
        （0.1.0 里它就是后门：先写 6MB，再 append 4MB，回执 10MB 照发）。

        原子性靠 tempfile + os.replace：半截文件比写失败更难查 —— 下一轮读到一个
        截断的 CSV，只会以为「文件坏了」，然后越修越乱。

        它**不是并发锁**：读旧账 → 判断 → 落盘之间有时间窗（TOCTOU），两个进程同写
        同一个文件，后写的赢，先写的那次回执照样有效。要多进程安全请在闸外面加锁。
        """
        self._check_env()
        path = self.resolve(rel)
        shown = self.show(path)
        base = _basename(shown)

        data = str(text if text is not None else "").encode("utf-8")

        # 顺序是刻意的：先判「名字对不对」，再判「内容空不空」。
        # 反过来的话，模型拿一个错名字 + 空内容来探路，会先挨一句「内容是空的」，
        # 改完内容再来一次才知道名字也错 —— 同一件事两轮对话，白烧一倍 Token。
        self._check_delivery(base)            # 闸一：名字
        self._check_not_blank(text, shown)    # 闸二：有没有可见内容

        if len(data) > MAX_WRITE_BYTES:
            raise WriteRefused("too_large", shown,
                               f"{len(data)} 字节，超过单次上限 {MAX_WRITE_BYTES}")

        existed = path.is_file()
        if existed and not append:
            self.guard_overwrite(rel, text)   # 闸三：防误删

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if append and existed:
                data = path.read_bytes() + data
        except OSError as e:
            raise WriteNotVerified(shown, data, b"") from e

        # 追加完再看总量。放在读旧文件之后、落盘之前：拒了也一样一个字节没动。
        if len(data) > MAX_WRITE_BYTES:
            raise WriteRefused(
                "too_large", shown,
                f"追加后共 {len(data)} 字节，超过单次上限 {MAX_WRITE_BYTES}。"
                "上限按写完之后的大小算 —— append 不能拿来绕过它。")

        gap = self._minimum_gap(data, base)
        if gap:
            raise WriteRefused(
                "too_small", shown,
                f"契约要求这份交付{self._minimum_hint()}，{gap}。"
                "占位文件、只写一句「已完成」都算没交。")

        tmp = ""
        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".vac-", suffix=".part")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())      # 落盘，不是落页缓存
            os.replace(tmp, path)
            tmp = ""
        except OSError as e:
            if tmp:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)   # 清不掉临时文件是次要的，别盖住真正的写失败
            raise WriteNotVerified(shown, data, b"") from e

        # 判据只有一条：盘上的字节 == 我打算写的字节。
        try:
            back = path.read_bytes()
        except OSError as e:
            raise WriteNotVerified(shown, data, b"") from e
        if back != data:
            raise WriteNotVerified(shown, data, back)

        sha = hashlib.sha256(data).hexdigest()
        if base in self.contract_names:
            self._delivered[base] = (shown, sha)

        # 回执上这一格也走 `_audit()`：内存里所有名字都在，不代表盘上都在。
        # 以前这里按 `_delivered` 算，交齐之后删掉一份，回执照样盖「交付完成」的章 ——
        # 和 delivery_state 的假绿灯是同一个病，只是长在另一个字段上。
        complete = self._audit()["complete"]
        return Receipt(
            path=shown, abspath=path, bytes=len(data), lines=_count_lines(data),
            sha256=sha, existed=existed,
            overwritten=existed and not append, appended=bool(append and existed),
            delivery_complete=complete)


# ── 默认工作区 + 模块级函数：一行就能用 ── 多 Agent / 多任务请用 Workspace 实例
_DEFAULT: Workspace | None = None


def default_workspace(root=None, required_files: Iterable[str] | None = None,
                      *, min_bytes: int = 0, min_lines: int = 0) -> Workspace:
    """拿到默认工作区。给了参数就重建（等价于「开新任务」，只能由开发者调）。"""
    global _DEFAULT
    if _DEFAULT is None or root is not None or required_files is not None:
        _DEFAULT = Workspace(root if root is not None else "workspace",
                             required_files=required_files,
                             min_bytes=min_bytes, min_lines=min_lines)
    return _DEFAULT


def init_task(required_files: Iterable[str], *, min_bytes: int = 0,
              min_lines: int = 0, ws: Workspace | None = None) -> Contract:
    """开发者专用：给默认工作区设定并封印交付契约。不暴露给模型。"""
    return (ws or default_workspace()).init_task(required_files, min_bytes=min_bytes,
                                                 min_lines=min_lines)


def guard_overwrite(rel, text: str, *, ws: Workspace | None = None) -> None:
    """旧账有数据、新货是空壳 → 抛 WriteRefused。也可以单独当预检用。"""
    (ws or default_workspace()).guard_overwrite(rel, text)


def write_and_verify(rel, text: str, *, append: bool = False,
                     ws: Workspace | None = None) -> Receipt:
    """写盘 + 读回逐字节比对。返回 Receipt，失败抛异常。"""
    return (ws or default_workspace()).write_and_verify(rel, text, append=append)
