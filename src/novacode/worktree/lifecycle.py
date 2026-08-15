"""Worktree 生命周期：enter / exit / remove / auto_cleanup（ch14 T6，spec F11-F15）。

作为 `Manager` 的 mixin（LifecycleMixin），经 `self` 访问 Manager 字段。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .git import _has_worktree_changes, _run_git
from .session import WorktreeSession, save_session

if TYPE_CHECKING:  # 仅类型检查解析，避免模块级循环
    from .manager import Worktree


class ExitAction(str, Enum):
    """退出动作：保留或删除（spec F12）。"""

    KEEP = "keep"
    REMOVE = "remove"


@dataclass
class ExitOptions:
    """退出选项：是否跳过变更保护（spec F12）。"""

    discard_changes: bool = False


@dataclass
class ExitReport:
    """退出结果（spec F12 步骤 6）。"""

    removed: bool
    path: str
    branch: str


@dataclass
class AutoCleanupReport:
    """自动清理结果（spec F14）。"""

    kept: bool
    path: str = ""
    branch: str = ""


class WorktreeHasChangesError(Exception):
    """Worktree 有未提交修改或本地多于 base 的 commit。"""


class LifecycleMixin:
    """Manager 的 enter / exit / remove / auto_cleanup 方法（spec F11-F15）。"""

    # mixin 访问 Manager 字段（由 Manager.__init__ 初始化；注解供类型检查）
    repo_root: str
    session_file: str
    active: dict[str, Worktree]
    current_session: WorktreeSession | None
    lock: asyncio.Lock

    async def enter(self, name: str) -> WorktreeSession:
        """进入 Worktree（spec F11）：记录会话状态并持久化，**不调 os.chdir**。"""
        async with self.lock:
            wt = self.active.get(name)
            if wt is None:
                raise ValueError(f"Worktree 不存在: {name}")
            original_cwd = str(Path.cwd())
            original_branch, original_head = await self._current_branch_head()
            session = WorktreeSession(
                original_cwd=original_cwd,
                worktree_path=wt.path,
                worktree_name=name,
                original_branch=original_branch,
                original_head_commit=original_head,
                session_id=secrets.token_hex(8),
            )
            self.current_session = session
            save_session(Path(self.session_file), session)
            return session

    async def exit(self, name: str, action: ExitAction, opts: ExitOptions) -> ExitReport:
        """退出当前 Worktree（spec F12）。只能退当前 session；REMOVE 时走变更保护。"""
        async with self.lock:
            if self.current_session is None or self.current_session.worktree_name != name:
                raise ValueError(f"只能退出当前 Worktree: {name}")
            wt = self.active.get(name)
            if wt is None:
                raise ValueError(f"Worktree 不存在: {name}")
            if action == ExitAction.REMOVE and not opts.discard_changes:
                changed = await _has_worktree_changes(wt.path, wt.head_commit)
                if changed:
                    raise WorktreeHasChangesError(
                        "Worktree 有未提交修改或本地新增 commit，拒绝删除；加 --discard 强制"
                    )
            # 兜底切回原 cwd（防御性；项目进程 cwd 本身从不改变，N4）
            with contextlib.suppress(OSError):
                os.chdir(self.current_session.original_cwd)
            self.current_session = None
            save_session(Path(self.session_file), None)
            if action == ExitAction.REMOVE:
                await _run_git(self.repo_root, "worktree", "remove", "--force", wt.path)
                await asyncio.sleep(0.1)  # git lockfile 竞态经验值
                await _run_git(self.repo_root, "branch", "-D", wt.branch)
                del self.active[name]
            return ExitReport(removed=action == ExitAction.REMOVE, path=wt.path, branch=wt.branch)

    async def remove(self, name: str, opts: ExitOptions) -> None:
        """独立 remove（spec F13）：允许删除非当前 session 的 Worktree，变更保护同 F12。"""
        async with self.lock:
            wt = self.active.get(name)
            if wt is None:
                raise ValueError(f"Worktree 不存在: {name}")
            if not opts.discard_changes:
                changed = await _has_worktree_changes(wt.path, wt.head_commit)
                if changed:
                    raise WorktreeHasChangesError(
                        "Worktree 有未提交修改或本地新增 commit，拒绝删除；加 --discard 强制"
                    )
            if self.current_session is not None and self.current_session.worktree_name == name:
                with contextlib.suppress(OSError):
                    os.chdir(self.current_session.original_cwd)
                self.current_session = None
                save_session(Path(self.session_file), None)
            await _run_git(self.repo_root, "worktree", "remove", "--force", wt.path)
            await asyncio.sleep(0.1)
            await _run_git(self.repo_root, "branch", "-D", wt.branch)
            del self.active[name]

    async def auto_cleanup(self, name: str) -> AutoCleanupReport:
        """SubAgent 退出后自动清理（spec F14）。手动创建的（manual=True）直接保留。"""
        wt = self.active.get(name)
        if wt is None:
            return AutoCleanupReport(kept=True)
        if wt.manual:
            return AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)
        changed = await _has_worktree_changes(wt.path, wt.head_commit)
        if not changed:
            await self.remove(name, ExitOptions(discard_changes=True))
            return AutoCleanupReport(kept=False)
        return AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)

    async def _current_branch_head(self) -> tuple[str, str]:
        """读主仓库当前分支与 HEAD；失败用空串兜底（spec F11 步骤 2）。"""
        try:
            branch = await _run_git(self.repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        except Exception:  # noqa: BLE001 兜底
            branch = ""
        try:
            head = await _run_git(self.repo_root, "rev-parse", "HEAD")
        except Exception:  # noqa: BLE001 兜底
            head = ""
        return branch, head
