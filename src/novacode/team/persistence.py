"""Team 持久化：sanitize / 原子写 / 磁盘 reload（ch15 T2/T3b，spec F63/F19c）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def sanitize(name: str) -> str:
    """把团队名清洗为可用于路径的 slug：只保留 `[a-zA-Z0-9._-]`，其他替换为 `-`，首尾去 `-`。"""
    s = re.sub(r"[^a-zA-Z0-9._-]", "-", name.strip())
    return s.strip("-")


def atomic_write_json(path: str | Path, value: Any) -> None:
    """原子写 JSON：先写 `<path>.tmp` 再 `os.replace`（跨平台原子）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def read_json(path: str | Path) -> Any:
    """读 JSON；文件不存在抛 FileNotFoundError。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def reload_from_disk_locked(team) -> None:
    """调用方持锁；从 config_path 重读 members 覆盖内存（失败静默回退内存现状）。

    意义（F19c）：Pane 后端的 Lead 与子进程是不同进程，各持一份内存 Team。写前重读
    disk 才能看到对方进程刚写入的成员，避免"set_member_active 静默 no-op"。
    """
    from .types import TeammateInfo

    try:
        data = read_json(team.config_path)
        members = data.get("members") or []
        if isinstance(members, list):
            team.members = [TeammateInfo.from_dict(m) for m in members if isinstance(m, dict)]
    except (OSError, ValueError, KeyError, TypeError):
        pass  # 读失败静默回退内存现状
