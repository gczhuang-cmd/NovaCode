"""路径沙箱：把文件类工具的操作范围限定在项目根内（N2）。

核心原则：**先解析符号链接、再做前缀比对**，防止用软链接指向项目外的目录绕过。
对尚不存在的目标（新建文件、含未创建中间目录），解析其**最近已存在的祖先目录**
后再判断，不因目标不存在而误判。用 pathlib / os.sep，不硬编码 `/`。
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_root(root: str) -> str:
    """项目根解析为绝对路径并展开符号链接；目录不存在时抛 FileNotFoundError。"""
    return str(Path(root).expanduser().resolve(strict=True))


def eval_symlinks_or_ancestor(path: str) -> str:
    """把路径解析为不含符号链接的绝对路径。

    - 目标存在（或可解析）：直接 `Path.resolve(strict=True)`（展开符号链接）；
    - 目标不存在：逐级回退到最近**已存在**祖先目录 `resolve(strict=True)` 后拼回
      剩余段，覆盖「新建文件、含未创建中间目录」的情形。
    """
    p = Path(path)
    try:
        return str(p.resolve(strict=True))
    except (FileNotFoundError, OSError):
        pass
    # 回退：逐级向上找最近已存在祖先并 resolve，再拼回剩余段
    suffix: list[str] = []
    cur = p
    while True:
        try:
            base = cur.resolve(strict=True)
            break
        except (FileNotFoundError, OSError):
            suffix.insert(0, cur.name)
            parent = cur.parent
            if parent == cur:
                # 到达根目录仍失败（极罕见），退化为按字面绝对路径
                return os.path.abspath(path)
            cur = parent
    return os.path.join(base, *suffix)


# ch15 N9：系统临时目录白名单——允许 file-class 工具（read_file/write_file/edit_file）
# 写入项目根之外的 /tmp（及 macOS 真实路径 /private/tmp）。工具脚本和队员经常用 /tmp 中转。
# bash 走 exec-class 权限，不受沙箱约束，无需在此处理。
TMP_WHITELIST_PREFIXES: tuple[str, ...] = ("/tmp/", "/private/tmp/")


def sandbox_ok(root: str, path: str) -> bool:
    """目标（绝对或相对）是否落在项目根内（或系统临时目录白名单）。

    空 path 视为 root 本身；相对路径相对 root 解析为绝对；先解析符号链接再前缀比对。
    """
    if not path:
        return True
    p = Path(path)
    # ch15 N9：系统临时目录白名单——用原始路径（POSIX 风格）判断，避免 Windows 把
    # `/tmp` resolve 成 `C:\tmp` 导致前缀不匹配（在 resolve/拼接前检查）。
    raw = str(path).replace("\\", "/")
    if raw.startswith("/"):
        for prefix in TMP_WHITELIST_PREFIXES:
            base = prefix.rstrip("/")
            if raw == base or raw.startswith(prefix):
                return True
    if not p.is_absolute():
        p = Path(root) / p
    resolved = eval_symlinks_or_ancestor(str(p))
    root_resolved = Path(root).resolve(strict=True)
    return resolved == str(root_resolved) or resolved.startswith(str(root_resolved) + os.sep)
