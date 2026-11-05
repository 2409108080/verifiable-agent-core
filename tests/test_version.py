"""版本号对账 —— `vac.__version__` 和 pyproject.toml 必须是同一串字符。

起因是一桩真事：0.1.3 发上 PyPI 之后，`pip show` 说 0.1.3，`vac.__version__`
还说 0.1.2 —— 两边都没报错，谁也没发现，直到有人 `import vac` 看了一眼。

一个宣称「自我声明不算数、只认盘上字节」的库，自己的版本号不能是自说自话。
所以这里不写「记得同步」，而是让它对不上就直接红。

只对 pyproject 一个来源，不去对 dist-info：editable 安装的 dist-info 在发版后
必然落后一次（改完 pyproject 还没重装），那条断言会在完全正常的情况下常红，
而常红的守卫等于没有守卫。pyproject 对了，构建出来的元数据就对了。
"""
from __future__ import annotations

import re
from pathlib import Path

import vac

ROOT = Path(__file__).resolve().parents[1]


def _declared_version() -> str:
    """读 pyproject.toml 的 [project].version。

    3.10 没有 tomllib（3.11 才进标准库），所以退化成正则；本项目零依赖，
    不为一个测试把 tomli 拉进来。两条路读的是同一个文件里的同一行。
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        assert m, "pyproject.toml 里找不到 [project].version"
        return m.group(1)
    return tomllib.loads(text)["project"]["version"]


def test_version_matches_pyproject() -> None:
    declared = _declared_version()
    assert vac.__version__ == declared, (
        f"vac.__version__ = {vac.__version__!r}，但 pyproject.toml 写的是 {declared!r}。"
        " 版本号得一起改 —— 发出去的包不能自己谎报版本。"
    )
