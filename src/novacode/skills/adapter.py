"""skills → prompt 桥接类型与转换（ch11 T9）。

prompt 包不反向依赖 skills 包，故用本包定义的 frozen dataclass 作为数据传递载体。
"""

from __future__ import annotations

from dataclasses import dataclass

from .active import ActiveSkills
from .catalog import Catalog


@dataclass(frozen=True)
class PromptItem:
    """prompt 包用的 Skill 目录项（第一层列表）。"""

    name: str
    description: str


@dataclass(frozen=True)
class PromptEntry:
    """prompt 包用的已激活 Skill 项（第二层 env 块）。"""

    name: str
    body: str


def catalog_to_prompt_items(c: Catalog) -> list[PromptItem]:
    """按 Catalog 字典序输出目录项。"""
    return [PromptItem(s.meta.name, s.meta.description) for s in c.list()]


def active_to_prompt_entries(a: ActiveSkills) -> list[PromptEntry]:
    """按激活顺序输出已激活项。"""
    return [PromptEntry(e.name, e.body) for e in a.snapshot()]
