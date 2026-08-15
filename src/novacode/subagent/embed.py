"""内置 Agent 定义读取（ch13 T5）：importlib.resources 读 builtin/*.md。

内置定义解析失败直接 raise（代码 bug，启动期失败即灾难，spec N4）。
"""

from __future__ import annotations

from importlib.resources import files

from .definition import Definition, Source
from .parser import parse_definition


def builtin_definitions() -> list[Definition]:
    """读取内置 3 个角色定义，返回按 name 升序的列表。"""
    pkg = files("novacode.subagent.builtin")
    defs: list[Definition] = []
    for entry in pkg.iterdir():
        if not entry.name.endswith(".md"):
            continue
        data = (pkg / entry.name).read_bytes()
        defs.append(parse_definition(data, f"builtin:{entry.name}", Source.BUILTIN))
    return sorted(defs, key=lambda d: d.name)
