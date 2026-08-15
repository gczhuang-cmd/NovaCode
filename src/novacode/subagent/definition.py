"""SubAgent 角色定义数据结构（ch13 T1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from ..permission import Mode


class Source(IntEnum):
    """定义来源层级：内置 < 用户 < 项目；插件占位（spec F5/F8）。"""

    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3  # 占位：本期不实现真插件加载

    def __str__(self) -> str:
        return {0: "builtin", 1: "user", 2: "project", 3: "plugin"}.get(int(self), "unknown")


@dataclass
class Definition:
    """一个 Agent 角色的完整定义，从 Markdown+YAML frontmatter 解析（spec F4）。"""

    name: str  # frontmatter.name（-> agent_type）
    description: str  # frontmatter.description（-> when_to_use）
    tools: list[str] = field(default_factory=list)  # frontmatter.tools 白名单；空表示不收窄
    disallowed_tools: list[str] = field(default_factory=list)  # frontmatter.disallowedTools 黑名单
    model: Literal["haiku", "sonnet", "opus", "inherit"] = "inherit"  # 本期解析但不切 provider
    max_turns: int = 0  # 0 表示沿用全局默认（MAX_ITERATIONS=25）
    permission_mode: Mode = Mode.DEFAULT  # 默认模式；"dontAsk" 由 dont_ask 字段单独表达
    dont_ask: bool = False  # 是否启用"绕过 Ask"的子 Agent 兜底模式
    background: bool = False  # 强制后台
    isolation: str = ""  # ch14："" 或 "worktree"（隔离到独立 Git Worktree 副本）
    plan_mode_required: bool = False  # ch15：作为 Team 队员 spawn 时以 plan 模式起步（F48）
    system_prompt: str = ""  # Markdown body（去 frontmatter 后的全文）
    file_path: str = ""  # 定义文件绝对路径（用于调试）
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        """Fork 路径临时定义的判别（name="__fork__"）。"""
        return self.name == "__fork__"
