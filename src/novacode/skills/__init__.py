"""Skill 技能包系统（ch11）。

对外接口：`Catalog` / `ActiveSkills` / `Skill` / `SkillMeta` / `SkillSource` /
`ToolSpec` / `render_body` / 桥接函数。`Executor` 在 executor.py（T19 接入后导出）。
"""

from __future__ import annotations

from .active import ActiveSkills
from .adapter import PromptEntry, PromptItem, active_to_prompt_entries, catalog_to_prompt_items
from .catalog import Catalog, ValidationIssue
from .executor import Executor
from .render import render_body
from .types import ActiveEntry, Skill, SkillMeta, SkillSource, ToolSpec

__all__ = [
    "ActiveEntry",
    "ActiveSkills",
    "Catalog",
    "Executor",
    "PromptEntry",
    "PromptItem",
    "Skill",
    "SkillMeta",
    "SkillSource",
    "ToolSpec",
    "ValidationIssue",
    "active_to_prompt_entries",
    "catalog_to_prompt_items",
    "render_body",
]
