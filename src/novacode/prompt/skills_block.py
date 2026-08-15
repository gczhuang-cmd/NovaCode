"""Skill 相关的 prompt 渲染函数与桥接类型（ch11 T11）。

prompt 包不反向依赖 skills 包：用本模块定义的 frozen dataclass 接收 skills 包
adapter 转换后的数据（`skills.catalog_to_prompt_items` / `active_to_prompt_entries`）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillCatalogItem:
    """第一层（system prompt）Skill 目录项。"""

    name: str
    description: str


@dataclass(frozen=True)
class ActiveSkillEntry:
    """第二层（env context）已激活 Skill 项。"""

    name: str
    body: str


def render_skills_catalog(items: list[SkillCatalogItem]) -> str:
    """第一层：Skill 名字 + 描述列表；空返回空字符串（F21/N2）。"""
    if not items:
        return ""
    lines = ["## Available Skills", ""]
    lines += [f"- {it.name}: {it.description}" for it in items]
    lines += [
        "",
        (
            'Call the LoadSkill tool with {"name": "<skill_name>"} to activate a '
            "skill's full SOP and specialized tools before executing it."
        ),
    ]
    return "\n".join(lines)


def render_active_skills_block(entries: list[ActiveSkillEntry]) -> str:
    """第二层：已激活 Skill 的 SOP 块；空返回空字符串（F22/N3）。"""
    if not entries:
        return ""
    parts = ["## Active Skills"]
    for e in entries:
        parts += ["", f"### Skill: {e.name}", "", e.body]
    return "\n".join(parts)
