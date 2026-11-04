"""校验 MANIFEST.sha256：文件都在、内容没被改过。

为什么不直接用 `sha256sum -c`：清单里的哈希是对 **LF** 字节算的。同一份仓库在
Windows 上被 git 检出时，文本文件的 LF 会变成 CRLF，字节变了、哈希就整份红 ——
那不是「文件被改了」，是「你换了个系统」。所以文本文件按 LF 归一后再比；
清单自己是二进制（见 .gitattributes 里的 `MANIFEST.sha256 -text`），不归一。

用法：
    python tools/check_manifest.py [仓库根目录]

退出码 0 = 全部一致；1 = 有缺失或对不上。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

MANIFEST = "MANIFEST.sha256"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def check(root: Path) -> tuple[int, int]:
    """返回 (一致的文件数, 有问题的文件数)。只打印，不抛。"""
    manifest = root / MANIFEST
    if not manifest.is_file():
        print(f"MISSING  {MANIFEST}（根目录：{root}）")
        return 0, 1

    ok = bad = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError:
            print(f"BADLINE  {line}")
            bad += 1
            continue
        path = root / name
        if not path.is_file():
            print(f"MISSING  {name}")
            bad += 1
            continue
        raw = path.read_bytes()
        # 两种都认：原样字节，以及 CRLF→LF 归一后的字节。
        if _sha(raw) == digest or _sha(raw.replace(b"\r\n", b"\n")) == digest:
            ok += 1
        else:
            print(f"CHANGED  {name}")
            bad += 1
    return ok, bad


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    ok, bad = check(root)
    print(f"{ok} 个一致，{bad} 个有问题")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
