"""Skill 技能包数据结构（ch11 T1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


class SkillSource(Enum):
    """Skill 来源层级：内置 < 用户 < 项目（F13）。"""

    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"

    def __str__(self) -> str:
        return self.value


@dataclass
class SkillMeta:
    """SKILL.md frontmatter 解析结果（F2~F8）。"""

    name: str = ""
    description: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    fork_context: Literal["none", "recent", "full"] = "none"
    model: str | None = None

    def is_fork(self) -> bool:
        return self.mode == "fork"


@dataclass
class ToolSpec:
    """tool.json 中声明的专属工具（F10）。"""

    name: str
    description: str
    input_schema: dict
    command: list[str]  # argv，首元素相对 base_dir 解析（或绝对路径）
    base_dir: Path  # exec 时的 cwd，固定为 Skill 目录


@dataclass
class Skill:
    """一个加载完成的 Skill（F1）。"""

    meta: SkillMeta
    prompt_body: str  # SKILL.md 去 frontmatter 后的正文（启动缓存，执行时重读覆盖）
    source_dir: Path  # 绝对路径或包资源路径，重读 SKILL.md 用
    source: SkillSource
    tool_specs: list[ToolSpec] = field(default_factory=list)


@dataclass
class ActiveEntry:
    """一条已激活 Skill 记录（F24）。"""

    name: str
    body: str
