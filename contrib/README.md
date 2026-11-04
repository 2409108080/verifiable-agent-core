# contrib/ —— 未经真机验证的框架包装（仅供参考）

这里的文件是**参考实现**，不是承诺。它们只做一件事：把 `vac` 的
`write_and_verify` 包成某个 Agent 框架认识的工具形状。

**核心只维护 [`../adapters/openai_function.py`](../adapters/openai_function.py)。**
那是唯一一个在真机上跑过四条分支（拦截 / 放行 / 交齐后拒绝 / 解封）的适配器，
因为它只需要 JSON schema，不依赖任何框架的版本脾气。

| 文件 | 目标框架 | 状态 |
|---|---|---|
| `langchain_tool.py` | `langchain-core` | **真机跑过**（langchain-core 1.7.4）：`write_file.invoke` 写 `README_final.md` 被拒、写 `report.md` 通过 |
| `llamaindex_tool.py` | `llama-index-core` | 未验证，仅语法检查 |
| `crewai_tool.py` | `crewai` | 未验证，仅语法检查 |
| `autogen_tool.py` | `autogen` / `pyautogen` | 未验证，仅语法检查 |
| `mcp_server.py` | `mcp`（FastMCP） | 未验证，仅语法检查 |

「未验证」的意思很具体：**我没有在装好该框架的环境里真正调用过一次这个工具。**
所以它可能因为版本差异、导入路径变化、装饰器签名改动而根本跑不起来 ——
你如果要用，第一件事是自己跑一次拦截，别相信这张表。

## 用之前的最小验收（三行）

不管用哪个框架，接上之后先做这件事，通过了再谈别的：

```python
# 1. 写一个**不在**清单里的名字，必须失败
assert "FAILED" in tool.invoke({"path": "README_final.md", "content": "x"})
# 2. 写清单里的名字，必须成功
assert "OK" in tool.invoke({"path": "report.md", "content": "x"})
# 3. 盘上只能有第 2 步那个文件
import pathlib; assert sorted(p.name for p in pathlib.Path("workspace").glob("*")) == ["report.md"]
```

第 1 步过了、第 3 步也过了，才算「这个适配器真的挂上了闸」。
只看到工具能被注册、能被调用，不算 —— 一个永远返回 `OK` 的假工具也能做到那个。

## 为什么不放在 adapters/ 里

因为它们没被跑过。把没跑过的东西和跑过的东西放在同一个目录、用同一种口气介绍，
读者会以为它们是一样可靠的 —— 上一版就是这么干的，六个包装里五个只过了语法检查，
README 却写得像六个都能用。宁可少给，也不给假货。
