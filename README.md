# Verifiable-Agent-Core

[![test](https://github.com/2409108080/verifiable-agent-core/actions/workflows/test.yml/badge.svg)](https://github.com/2409108080/verifiable-agent-core/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://github.com/2409108080/verifiable-agent-core)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A ~600-line delivery gate for agents. It turns "I wrote it" from a self-report into a call that can fail.**

No framework. No model calls. No API key. Standard library only. `import` and go.

> 你让 agent 跑一晚上交文件，第二天早上打开文件夹，里面应该只有你要的那几份东西。
> 名字不对、内容是空壳、只有零宽字符、旧账被空壳覆盖 —— 四种都在写入路径上被拒。
> **拒了那一次，盘上一个字节都没动。**

```bash
pip install verifiable-agent-core     # 不是 `vac`：那个名字是别人的包
```

```python
from vac import Workspace, WriteRefused

ws = Workspace("work", required_files=["report.md", "summary.csv"])   # 名单只由部署方给

ws.write_and_verify("report.md", "# 本周复盘\n\n总作品 25 件。\n")   # → Receipt(verified=True)
ws.verify_all_delivered()                                             # False：还欠 summary.csv
ws.write_and_verify("README_final.md", "...")                         # → WriteRefused(delivery)
```

## 边界：它只挡一个动词

**`write_file`。** 只要它是你给 Agent 的唯一落盘路径，这把锁就是闭合的 —— 名字不对、
内容空、旧账被空壳盖掉，都在调用返回之前被拒，模型只能拿着拒绝理由改对再来。

**如果 Agent 还有第二条落盘的路，本库既拦不住也看不见**：shell、代码沙箱、浏览器下载、
你自己加的文件工具、同一个 MCP 里另挂的文件系统 server，都算。这不是藏起来的缺陷，是
适用范围 —— **接线只接一个写口，闸才叫闸；接了两个，另外那个就是正门。**

同理，它不判断内容对不对：四段「待补充」凑够 `min_bytes`，四道闸全绿。下限保证的是
「像份东西」，不是「东西对」——**那是评分器的活，别指望闸替你判对错。**

> ⚠️ **V0.1.3（实验性）**：连 1.0 都还没到。四道闸可用，但多进程并发、跨机器、
> 第二条写入路径这些边界都没解决 —— 先看完这一节再决定要不要用。

---

## 它不做什么（逐条，别跳）

- **不提高模型能力。** 它只在交付这一环设卡。推理错了、数据算错了，它拦不住。
- **不管思考过程。** 没有规划器、没有记忆、没有反思循环。那些是 Agent 框架的事。
- **只管文件名，不管路径。** 契约点名 `report.md`，写到 `deep/nested/report.md`
  **也算已交付** —— 匹配用的是 basename。这是刻意的克制（不做全路径推理，就不会有
  「我觉得这个路径等价」这种模糊地带），代价是路径伪装拦不住。**本库没有位置校验**：
  想知道某一次到底写到了哪，看 `Receipt.path`，别指望闸替你判断目录对不对。
- **空文件、占位文件都会被拒，但「有内容」不等于「内容对」。** 空/纯空白无条件拒绝；
  契约还能要求 `min_bytes` / `min_lines`。再往上一层的对错（数据算得对不对、结论站不站得住）
  它管不了 —— 那是评分器的事。
- **交齐不等于解封，但 `end_task()` 之后就真不管了。** 清单交齐之后，写清单外的名字
  仍然被拦 —— 放开权限的动作只有一个（`end_task()`），而且在部署方手里。代价是调用它
  之后本实例不再有任何契约：后续写入不受管，`verify_all_delivered()` 也会变成 `False`
  （没契约可验）。**验收请用 `end_task()` 的返回值**，要接着做下一件事就 `init_task()`。
- **不是安全沙箱，是一个「可验证执行层」。** `resolve()` 只防路径跑出工作区
  （含符号链接/junction），不防恶意代码。**Agent 如果拥有终端权限，可以直接绕过本库**
  —— 自己 `open()` 写盘、删掉文件、改进程内存，都拦不住。在不可信环境里跑，请套真正的容器。
- **只保证单进程写入校验，不保证多进程并发下的数据强一致性。** 「读旧文件 → 判断 → 落盘」
  之间有时间窗（TOCTOU）：两个进程同时写同一个文件，后写的赢，先写的那次回执仍然有效。
  要多进程安全，请在闸外面加文件锁（Windows `msvcrt.locking` / POSIX `fcntl.flock`）。
- **不证明性能提升。** `examples/` 里的 93:100 是**历史存档复算（n=1 单局）**：
  同一道题、同一个裁判重算一遍。它不是 A/B 统计实验，不能当作提升幅度的绝对统计证明。
- **不防同进程内的代码执行。** 契约的「不可变」是 Python 对象级的：能在同一进程里跑任意
  Python 的模型，可以直接改内存、改 `os.environ` 的同时改快照。那种威胁模型下必须把校验
  放到进程外（另一台机器、另一个用户、只读挂载或独立审计进程）。
- **不替你判对错。** `judge_3state` 只保证「判不出来」不会被记成「成了」；
  至于 `True` 是不是真的对，取决于你喂进去的判据。

---

## 它修的是一个具体的病

Agent 跑完任务，回来跟你说「已完成，报告写在 report.md」。你去盘上看，有三种情况：

1. 文件不在 —— 它写到了自己顺手编的名字（`README_final.md`、`note.md`）；
2. 文件在，但里面是个空壳 —— 它把一份 10 条数据的热榜覆盖成了一行表头；
3. 文件在，内容也对 —— 但那是上一轮留下的，这一轮根本没写成。

这三种在返回值里长得**一模一样**：`ok: true`。

提示词治不了这个病。你在 prompt 里加十遍「必须写到 report.md」，它下一次采样
仍然可能写别的名字 —— 因为 prompt 是建议，采样是概率。**能治的只有拒绝**：
写错名字这一次调用就直接失败，模型拿到失败原因，重来。

这就是本库唯一的野心：把「交付」从一句自我声明，变成一个**会失败的调用**。

---

## 四个函数 + 一道验收

| 入口 | 管什么 | 失败时 |
|---|---|---|
| `Workspace(root, required_files=[...], min_bytes=, min_lines=)` / `init_task(names)` | 任务点名要交哪些文件、每份至少多大（**只有开发者能设**） | 写别的名字 → `WriteRefused("delivery")`；不到下限 → `WriteRefused("too_small")` |
| `guard_overwrite(path, text)` | 旧文件有真数据，新内容是空壳（`allow_shrink=True` 可关掉这道） | 拒绝覆盖 → `WriteRefused("overwrite")`，盘上原样不动 |
| `write_and_verify(path, text)` | 空内容检查 + 原子写盘 + 读回逐字节比对 | 空/纯空白 → `WriteRefused("empty")`；对不上 → `WriteNotVerified`，**不返回回执** |
| `judge_3state(outcome)` | 成了 / 没成 / 判不出来 | `None` 永远是 `unknown`，`if` 里为假 |
| `verify_all_delivered()` | 验收：点名的都在盘上、没被调包、够得着下限 | 返回 `False`（不抛），只认盘上的字节；没有契约也返回 `False` |
| `end_task()` | **唯一**能解除写入限制的钩子（只有开发者能调） | 返回本次任务的验收快照 |

`write_and_verify` 内部按顺序串了四道闸（交付契约 → 空内容 → 防误删锁 → 读回校验），
所以正常只需要调它一个。

---

## 30 秒上手

```bash
git clone https://github.com/2409108080/verifiable-agent-core
cd verifiable-agent-core && pip install -e .
# 只想先看一眼：在仓库根目录起 REPL 可以 import vac（纯 stdlib，零依赖）；
# 跑 examples/ 请先安装 —— 脚本另有路径兜底，但装过才是标准姿势
```

```python
from vac import Workspace, judge_3state, WriteRefused

# 名单在**构造时**给。这一步之后它只读，没有 API 能改宽或清空。
ws = Workspace("workspace", required_files=["report.md", "summary.csv"])

try:
    r = ws.write_and_verify("report.md", "# 本周复盘\n\n总作品 25 件。\n")
    print(r.verified, r.bytes, r.sha256[:8])    # True 36 40d876d7
except WriteRefused as e:
    print("被拒：", e.reason, e.detail)          # delivery / overwrite / escape / empty

ws.verify_all_delivered()                       # False —— 还欠 summary.csv
```

`Receipt.verified` 是构造时就为 `True` 的 —— 拿得到回执，就说明读回来了、且一字不差。

但 `Receipt.delivery_complete` 是另一回事，别混着读：它只回答「契约上的文件是不是全齐了」。
**没有契约时它恒为 `False`**（跟 `verify_all_delivered()` 同一条判据）—— 没设清单就没有
「齐了」这回事，一次普通写入不该拿到盖着「交付完成」的章。

---

## 看它拦一次

```bash
python examples/bench.py        # 真跑，不调模型，秒级
```

```
任务：把本周复盘交成 report.md 与 summary.csv。
（名单是部署方在构造 Workspace 时写死的，模型手里没有任何改它的工具）

──────────────────────────────────────────────────────────────────
第 1 次：模型自认为文件名「更好看」，写成 README_final.md
──────────────────────────────────────────────────────────────────
[model] write_file(path="README_final.md", ...)
[vac]   ✗ WriteRefused（reason=delivery）
        任务点名的交付文件还缺：report.md、summary.csv。要求的文件名是 report.md，
        不准自创「README_final.md」。先把点名的交齐，再写别的。
[disk]  workspace/ 里现在有：（空，一个字节都没落）

──────────────────────────────────────────────────────────────────
第 2 次：模型照拒绝理由改成 report.md
──────────────────────────────────────────────────────────────────
[model] write_file(path="report.md", ...)
[vac]   ✓ 写入并读回：36 字节 / 3 行 / sha256 40d876d7cd44…
[vac]   交付进度：['summary.csv'] 还缺

──────────────────────────────────────────────────────────────────
第 4 次：交齐了两份，模型想再塞一份 notes.md（旧版本这里会放行）
──────────────────────────────────────────────────────────────────
[model] write_file(path="notes.md", ...)
[vac]   ✗ WriteRefused（reason=delivery）
        清单上的文件都交齐了，但任务还没结束（未调用 end_task()），
        这里仍然只认清单内的名字，不准写「notes.md」。
```

上面每一行 `[vac]` 都是 `WriteRefused` 的原文，`[disk]` 是当场列出来的目录内容 ——
`demo_intercept()` 里没有任何一句是写死的旁白。

要点只有一句：**拒绝发生在写入路径上**。模型没法「说服」它放行，
只能拿着拒绝理由改对再来一次；而失败的那一次，一个字节都没落盘。

`python examples/bench.py --gates` 是另一组演示：空壳覆盖、空内容、读回不一致，四道闸各拦一次。

---

## 权限模型：钥匙不在被锁的人手里

这一节是这个库和「提示词里写纪律」的全部区别。

**病根**：如果模型手里有一个「登记交付清单」的工具，它就能先调
`set_delivery("")` 把闸拆了，再随便写。锁装在了门的同一侧 —— 这不是锁不结实，
是根本没锁。第一版就是这么错的，`set_delivery` 曾是模型可调的工具。

**现在的结构**：

- **名单只能从开发者通道进**：`Workspace(root, required_files=[...])`，或任务开始时
  调 `init_task([...])`。两个都不在任何适配器的工具表里。
- **设完即封印**：契约是 `frozen` 的 `Contract` 对象，清单是 `tuple`。
  没有 `set_delivery`、没有 `clear`、没有「传空字符串」这一说。
- **重设即异常**：任务没结束就再调 `init_task()`（无论谁调、传什么都算），
  抛 `ContractViolation`。要换任务，开发者先 `end_task()`，再 `init_task()`。
- **空清单直接拒绝**：`required_files=[]`、`[""]`、`init_task([])` 全部抛
  `ContractViolation` —— 「什么都不用交」等于给所有交付盖章，它不是一种任务。
- **改环境变量也算篡改**：封印瞬间会把 `VAC_DELIVERY` 快照记进契约；之后每次
  写入 / 验收都比对一次。改宽、改窄、删掉，一律 `ContractViolation`。
- **验收只认盘上的字节**：`verify_all_delivered()` 重新读文件、比 sha256，
  不信内存里那句「我写过了」。

```python
ws = Workspace("workspace", required_files=["report.md"])
ws.init_task([])                        # ContractViolation：已封印，任务没结束
ws.contract_names.append("x.md")        # AttributeError：tuple
ws.contract.names = ()                  # FrozenInstanceError：frozen dataclass
os.environ["VAC_DELIVERY"] = ""         # 下一次写入 → ContractViolation
```

模型能做的只剩一件事：调 `write_file` 往工作区里写。名字不对，这一次调用失败。

---

## 四道闸 + 一条判据，各拦一种真事故

**① 写错名字。** 任务要 `report.md`，它写了 `README_final.md`，内容全对。

```python
ws = Workspace("workspace", required_files=["report.md"])
ws.write_and_verify("README_final.md", "...")   # 内容再对也不收
# WriteRefused: [delivery] README_final.md: 任务点名的交付文件还缺：report.md。
#               要求的文件名是 report.md，不准自创「README_final.md」。
```

盘上一个字节都没动。模型拿到这句话，下一次就会写对 —— 这不是说服，是没得选。

**② 防误删锁：防的是手抖，不是恶意的渐进式覆盖。**

`hot.csv` 里躺着 10 条真实话题，它要写一行表头进去。

```python
ws = Workspace("workspace")      # 这个任务不做交付约束，单看这一道闸
# 旧文件 ≥4 行，且行数或字节塌掉一半以上（两条任一成立就拦）
ws.write_and_verify("hot.csv", "rank,topic,heat\n")
# WriteRefused: [overwrite] 这里存着 11 行内容（317 字节），你这次只写了 1 行（16 字节）。
#               行数从 11 掉到 1（不到一半）。
#               没有新数据就把旧账擦掉是自毁 —— 不许。
```

正常改写（相仿或更长）一律放行。判据用**新旧比例**而不是「新内容 ≤N 行」：
绝对阈值填两行长注释就能绕过（11 行真账 → 1 行表头 + 2 行废话，字节还变多了）。
代价是「11 行 CSV 原地改写成 1 行 JSON」会被拦 —— 这种改写请另存新名，
或者由开发者在构造时写 `Workspace(..., allow_shrink=True)` 主动关掉这一道（默认 `False`）。

它防的也不是「恶意的渐进式覆盖」：每轮删 10%，每轮都不到一半，这条锁永远不会响。
要防那种，得靠版本控制或 append-only 日志 —— 本库不假装自己能干这个。

**③ 空文件当交付。** 最便宜的一种假装：`write_and_verify("report.md", "")`。

```python
ws.write_and_verify("report.md", "")        # WriteRefused: [empty] 内容里没有任何可见字符。
ws.write_and_verify("report.md", " \n\t\n") # 一样拒 —— 空白不是内容
ws.write_and_verify("report.md", "\u200b")    # 零宽空格同样是空文件（0.1.2 补的）
# 契约还能加下限：占位文件、只写一句「已完成」都算没交
ws = Workspace("workspace", required_files=["report.md"], min_bytes=200, min_lines=5)
# WriteRefused: [too_small] 契约要求这份交付的内容（至少 200 字节、5 行），至少要 200 字节…
```

这一条跟有没有契约无关，是写入层的规矩。0.1.0 里它漏了，一个 0 字节的 `report.md`
能拿到 `verified=True` 的回执 —— 外面那轮盲测就是这么把包拆开的。

**④ 写了但没成。** 这是最隐蔽的一种：`os.replace` 返回了，`write()` 没报错，但盘上不是那个东西。

```python
r = ws.write_and_verify("report.md", text)   # 内部：mkstemp → write → fsync → replace → 读回
# 读回字节 != 写入字节 → 抛 WriteNotVerified，不给回执
```

所以本库**没有** `ok` 这个字段。只有 `Receipt`，或者异常。中间态不存在。

**⑤ 判不出来。** 三态的关键不是多一个值，是 `None` 不会被顺手当成成功：

```python
v = judge_3state(tool_result)
if v:            # 只有 passed 才进来；unknown 和 failed 都走 else
    ship_it()

judge_3state(None).state      # "unknown"
bool(judge_3state(None))      # False      ← 语言层兜底，不靠调用方守规矩
judge_3state({"ok": 1}).state # "unknown"  ← 1 == True，但不是 bool，不认
```

---

## 接进你现有的 Agent

适配器是独立文件，20~35 行，复制到你的项目里就能用。名单由部署方在
**进程启动前**给：环境变量 `VAC_DELIVERY="report.md,summary.csv"`，或直接改那一行
`Workspace(..., required_files=[...])`。下限也走同一条通道：`VAC_MIN_BYTES=200`、
`VAC_MIN_LINES=5`（只约束点名的文件；没有 `VAC_DELIVERY` 时会在 stderr 喊一声，
因为那时它不会起作用）。适配器里没有改契约的工具 —— 这是故意的。

**只维护一个适配器：**

| 适配器 | 接法 | 状态 |
|---|---|---|
| [`adapters/openai_function.py`](adapters/openai_function.py) | 纯 JSON schema，DeepSeek / Qwen / Moonshot / OpenAI 通用 | **真机跑过**：拦截、放行、交齐后拒绝、`end_task()` 解封四条分支都有实跑输出 |

其余框架的包装都挪进了 [`contrib/`](contrib/)，**除 LangChain 外均未经真机验证，仅供代码参考**：

| 参考实现 | 接法 | 状态 |
|---|---|---|
| [`contrib/langchain_tool.py`](contrib/langchain_tool.py) | `@tool` 装饰器 | 真机跑过（langchain-core 1.7.4） |
| [`contrib/llamaindex_tool.py`](contrib/llamaindex_tool.py) | `FunctionTool.from_defaults` | 未验证 |
| [`contrib/crewai_tool.py`](contrib/crewai_tool.py) | `@tool("write_file")` | 未验证 |
| [`contrib/autogen_tool.py`](contrib/autogen_tool.py) | `FunctionTool(...)` | 未验证 |
| [`contrib/mcp_server.py`](contrib/mcp_server.py) | MCP stdio 服务 | 未验证 |

宁可只给一个真能跑的，也不给六个只过了语法检查的包装 —— 那六个里每一个都能
让你以为「接上了」，然后在第一次真拦的时候才发现它压根没跑过。

共同的一条：**适配器只暴露 `write_file` 一个写入口**（MCP 那个另有一个只读的
`delivery_state`）。不暴露 `open()`、不暴露 `Path.write_text()` ——
有一个绕过校验的写口，闸就等于不存在。

### 旁路免责（重要）

**VAC 只能保护走 `write_file` 的通道。如果你给 Agent 开了别的文件系统直写权限，
本库不背锅。**

具体说，下面这些路都不经过闸，本库既拦不住也看不见：

- 你自己加的其他文件工具（`write_text` / `open(..., "w")` / `shutil` / `os.rename`）；
- Agent 自带的代码执行、Shell、Jupyter、浏览器下载落盘；
- 同一个 MCP 里另外挂的文件系统 server；
- 任何在进程内能执行任意 Python 的模型（它可以直接改内存，见下节）。

接进生产前请自查：**Agent 的工具表里，除了 `write_file`，还有没有第二个能落盘的入口。**

---

## 为什么这么设计

**契约用封印，不用「约定」。** 上一版把 `set_delivery` 当工具给模型，理由是「换任务方便」。
代价是模型可以用一行调用把闸关掉 —— 便利给错了人。现在换任务仍然方便，只是那个动作
回到了部署方手里（`end_task()` → `init_task()`）。核心也因此从 499 行涨到 671 行：
多出来的 170 余行几乎全在「封印 + 篡改检测 + 只认盘上字节的验收」上（另有几行是修回执假绿灯），
没有一行业务逻辑。

**拒绝用异常，不用返回值。** 返回值会被 `if not ok: continue` 顺手吞掉；异常必须被处理。
被拒绝和写坏了是两种事故，所以是两个异常类，`reason` 是机器可判的短码。

**闸自己出故障时放行。** 路径解析、行数计算、读取旧文件全部包在 try 里，出错就过。
锁的 bug 不能反过来把人锁在门外 —— 安全装置的失效方向必须是「不拦」，不是「全拦」。

**关掉锁不影响写入。** `Workspace(locks=False)` 就是去掉这层锁的同一个 Agent：
一样的写路径、一样的读回校验，只是不再拒绝。

但它**不再是干净的对照组**（0.1.2 起说明白）：空内容检查与锁无关，`locks=False`
也照拒空文件；契约下限则跟着 `locks` 一起下线。能对照的只有「防误删 + 交付契约 +
下限」这三样一起消失。要做 A/B，就得把两边的差别说全 —— 「提升多少」这个数应该是
跑出来的，不是写的。

**只有 stdlib。** 闸要在任何环境里都能立起来。为了一个 try/except 引入依赖，
等于在地基里埋一个可能装不上的东西。

---

---

## 附录：那局 93 : 100（历史存档，不是核心卖点）

> **注意：此分数为历史存档复算，物理闸不提升模型智商，只强制交付纪律。**

`python examples/bench.py --bench` 复算的是另一件事：一道 30 分钟的数据分析题，
四个交付文件，两个陷阱（T+8min 热榜服务真断网、T+13min 递来一份「总数 31」的假更正，
真账是 25）。同一个 Agent 跑两遍，**唯一差别是这层闸**。

```
==== A 组 · 裸 Agent（无闸）   得分 93/100 ====
  正常·report.md: 23/30  总数✓ 总赞✓ 最高作品✓ 核对差异✗
==== B 组 · 带 vac 物理闸   得分 100/100 ====
```

**这是 n=1 的单局存档复算，不是统计意义上的提升幅度。** 7 分差在一处：
裸 Agent 算对了所有数，但没核对「备份 23 件 vs 真账 25 件」这个差异。
它没有写错文件、没有编数据 —— 它只是漏了一次核验。

> **注意：7 分差距是因为裸 Agent 少写了一句核对差异，这是模型智商问题，不是 vac 物理闸的功劳。
> 物理闸的价值在于强制交付，而不是让模型变聪明。**
>
> 换句话说：这一局换成任何足够仔细的模型，裸跑也能拿 100。闸不生产正确，
> 它只让「没交、没核对、写了空壳」这三类事在验收时发出声音。

---

## 0.1.1：三个 P0（外面那轮盲测把包拆开的地方）

0.1.0 发出去之后被做了一轮盲测，三个洞都是真的。修法都写在上面的四道闸里，这里只留证据：

| 洞 | 0.1.0 的行为 | 0.1.1 |
|---|---|---|
| 空文件 | `write_and_verify("report.md", "")` → `verified=True`，0 字节也算交付 | 无条件 `WriteRefused("empty")`；契约可加 `min_bytes` / `min_lines` |
| 假绿灯 | 写完再 `unlink`，`delivery_state()["complete"]` 仍是 `True`，而 `verify_all_delivered()` 是 `False` | 两处共用同一个 `_audit()`，逐条回盘上读；`written` / `missing` / `unverified` 三格互斥 |
| CRLF | 清单哈希按 Windows 检出后的 CRLF 字节算，换台机器整份失配 | `.gitattributes` 强制 `eol=lf`，清单标 `-text`；校验器按 LF 归一后比对 |

顺带两个：防误删锁的行数判据从「新内容 ≤2 行」改成新旧比例（填两行长注释绕不过去了）；
`append=True` 不再能突破 8MB 上限（上限按**写完之后**的大小算）。

---

## 0.1.2：四个洞 + 一个逃生口

0.1.1 之后又被盲测了一轮，四个都是真的。没有加新功能，只有判据和顺序：

| 洞 | 0.1.1 的行为 | 0.1.2 |
|---|---|---|
| 零宽字符 | `write_and_verify("report.md", "\u200b")` 过 `strip()` → 拿到 `verified=True`，盘上是个看不见的文件 | 用 `unicodedata.category()` 逐字符判可见性：`Cc/Cf/Cs/Co/Cn/Zs/Zl/Zp/Mn/Me` 全算不可见 → `WriteRefused("empty")` |
| 报错顺序 | 错名字 + 空内容，先挨「内容为空」，补完再来一次才发现名字也错 | 交付契约提到内容检查之前，一次调用只暴露一个问题，省一轮 Token |
| 适配器下限 | `min_bytes` / `min_lines` 只能写死在代码里 | 读 `VAC_MIN_BYTES` / `VAC_MIN_LINES`，与 `VAC_DELIVERY` 同一条通道（import 时封印） |
| 误伤 | 11 行 CSV 原地改写成 1 行 JSON 一律被拦 | `Workspace(..., allow_shrink=True)` 由开发者主动关掉防误删锁（默认 `False`） |

零宽那条值得单独说一句：**判据要按语义写，不能按字符编码写。** `strip()` 回答的是
「这段文字两头有没有空白」，而我们要问的是「这份文件打开之后有没有东西」。两个问题
长得像，后者多出来的那一半（零宽空格、BOM、方向控制符、孤立组合符）就是洞。

`allow_shrink` 只关「防误删」这一道：空内容、交付契约、读回校验照样立着。
它是给部署方的，不是给模型的 —— 模型手里没有构造 `Workspace` 的参数。

---

## 0.1.3：一个红叉 + 第一屏（代码一行没动）

0.1.2 之后没有改判据、没有加闸。这一版只修「别人能不能用上」：

| 事 | 0.1.2 | 0.1.3 |
|---|---|---|
| Windows CI | 九个 job 里三个永远红 | 绿。根因不是代码：测试里的子进程会往 stdout 打中文，而 en-US 的 Windows 控制台代码页是 cp1252，编不出来直接把进程打挂。workflow 顶层加 `PYTHONUTF8=1`，两处 `subprocess.run` 显式 `encoding="utf-8"` |
| 第一屏 | 24KB 中文长文，第一行是警告 | 英文一句 + 30 秒示例 + **边界写在门口**（只挡 `write_file` 一个动词） |
| 发现路径 | 无 description / topics，未发 PyPI | 元数据补齐，`publish.yml` 用 OIDC 发版，不存 token |
| 清单 | `MANIFEST.sha256` 只在提交者本地对得上 | 改文件必重算（这次就重算了两次） |

**判据没变的证据**：60 项测试在 UTF-8 和 cp1252 两种环境下都是全绿；
`examples/bench.py` 两种都跑得出来；四道闸的代码一个字没改。

这也算一次自我验收：一个专治「声称」的库，改了文档就得把清单重算、把 CI 跑绿，
否则它自己就在犯它要治的病。

---

## 开发

```bash
pip install -e ".[dev]"
pytest -q          # 60 项，全离线
python tools/check_manifest.py   # 校验 MANIFEST.sha256（CRLF 检出也认）
ruff check .
```

MIT License.
