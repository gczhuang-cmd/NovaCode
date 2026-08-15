"""后台过期清理：sweep_stale + 随机临时名（ch14 T7，spec F33/F34）。

作为 `Manager` 的 mixin（SweepMixin），经 `self` 访问 Manager 字段。
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from .git import _is_setup_entry, _run_git
from .lifecycle import ExitOptions
from .session import WorktreeSession

logger = logging.getLogger(__name__)

# 只识别 SubAgent 临时 Worktree 命名（spec F33 第一层）
EPHEMERAL_PATTERN = re.compile(r"^agent-a[0-9a-f]{7}$")


def random_agent_name() -> str:
    """SubAgent 临时 worktree 名：`agent-a` + 7 位 hex（spec F21 步骤 1 / F33）。"""
    return "agent-a" + secrets.token_hex(4)[:7]


class SweepMixin:
    """Manager 的 sweep_stale 方法（spec F33）。"""

    # mixin 访问 Manager 字段/方法（由 Manager.__init__ 初始化 / LifecycleMixin 提供；注解供类型检查）
    worktree_dir: str
    current_session: WorktreeSession | None
    remove: Callable[[str, ExitOptions], Awaitable[None]]

    async def sweep_stale(self, cutoff: datetime) -> list[str]:
        """清理超时过期的临时 Worktree；有变更/未推送 commit 的 fail-closed 保留。"""
        removed: list[str] = []
        d = Path(self.worktree_dir)
        if not d.is_dir():
            return removed
        for p in sorted(d.iterdir()):
            if not p.is_dir() or not EPHEMERAL_PATTERN.fullmatch(p.name):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
            except OSError:
                continue
            if mtime > cutoff:
                continue
            if self.current_session is not None and self.current_session.worktree_path == str(p):
                continue
            if not await self._is_clean(p):
                continue  # fail-closed：有变更/未推送 commit 保留
            try:
                await self.remove(p.name, ExitOptions(discard_changes=True))
            except Exception as exc:  # noqa: BLE001 单个清理失败不中断整体 sweep
                logger.warning("worktree: sweep remove %s 失败: %s", p.name, exc)
                continue
            removed.append(p.name)
        return removed

    async def _is_clean(self, p: Path) -> bool:
        """变更检查：status 干净 且 无未推送 commit 才算干净；git 出错返回 False。

        无 remote 的仓库跳过「未推送」检查（`--not --remotes` 无排除目标时会列出 HEAD 自身，
        误判所有 commit 为未推送）。
        """
        try:
            status = await _run_git(str(p), "status", "--porcelain")
            # 忽略 setup 复制/软链条目（.novacode 配置 + 大目录软链，非真实变更）
            if any(not _is_setup_entry(line[3:]) for line in status.splitlines()):
                return False
            remotes = await _run_git(str(p), "remote")
            if remotes:
                unpushed = await _run_git(
                    str(p), "rev-list", "--max-count=1", "HEAD", "--not", "--remotes"
                )
                if unpushed:
                    return False
        except Exception:  # noqa: BLE001 fail-closed
            return False
        return True
