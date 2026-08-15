"""Worktree 隔离子系统（ch14）。

对外接口：`Manager` / `Worktree` / `WorktreeSession` / `validate_slug` / `flat_slug` /
`ExitAction` / `ExitOptions` / `ExitReport` / `AutoCleanupReport` / `WorktreeHasChangesError` /
`random_agent_name`。worktree 包只依赖标准库 + git，不依赖 agent / command / tui。
"""

from __future__ import annotations

from .lifecycle import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeHasChangesError,
)
from .manager import Manager, Worktree
from .session import WorktreeSession
from .slug import flat_slug, validate_slug
from .sweep import random_agent_name

__all__ = [
    "AutoCleanupReport",
    "ExitAction",
    "ExitOptions",
    "ExitReport",
    "Manager",
    "Worktree",
    "WorktreeHasChangesError",
    "WorktreeSession",
    "flat_slug",
    "random_agent_name",
    "validate_slug",
]
