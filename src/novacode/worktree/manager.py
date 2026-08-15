"""Worktree 数据结构 + Manager（ch14 T4，spec F2/F4/F5）。

Manager 通过 mixin 组合生命周期方法（CreateMixin / LifecycleMixin / SweepMixin），
本文件只负责：构造校验、session 恢复、active 扫描与查询。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .create import CreateMixin
from .git import _resolve_head_sha_from_fs
from .lifecycle import LifecycleMixin
from .session import WorktreeSession, clear_session, load_session
from .sweep import SweepMixin

# 创建后设置 C 默认软链的大目录（spec F4/G4）
DEFAULT_SYMLINK_DIRS: list[str] = ["node_modules", ".venv", "vendor"]


@dataclass
class Worktree:
    """单个 Worktree 的元信息（spec F2）。"""

    name: str  # 原始 slug（可能含 /）
    path: str  # 绝对路径
    branch: str  # worktree-<flat_slug>
    based_on: str  # 创建时的 base 引用（HEAD / SHA）
    head_commit: str  # 创建时的 commit SHA
    created: datetime
    manual: bool  # True=用户手动创建（/worktree create 路径）


class Manager(CreateMixin, LifecycleMixin, SweepMixin):
    """Worktree 生命周期管理器（spec F4/F5）。"""

    def __init__(self, repo_root: str) -> None:
        self.repo_root = str(Path(repo_root).resolve())
        self._verify_repo_root()
        self.worktree_dir = str(Path(self.repo_root) / ".novacode" / "worktrees")
        self.session_file = str(Path(self.repo_root) / ".novacode" / "worktree_session.json")
        self.symlink_dirs = list(DEFAULT_SYMLINK_DIRS)
        self.lock = asyncio.Lock()
        self.active: dict[str, Worktree] = {}
        self.current_session: WorktreeSession | None = None

        Path(self.worktree_dir).mkdir(parents=True, exist_ok=True)
        self._load_session_file()
        self._scan_worktrees()

    def _verify_repo_root(self) -> None:
        """repo_root 必须是 git 仓库根目录，否则抛 ValueError（spec F5，cli 据此降级）。"""
        out = subprocess.run(
            ["git", "-C", self.repo_root, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",  # git 输出恒 UTF-8；Windows 默认 GBK 会解码失败（ch13 坑）
            check=False,  # returncode 由下方显式判断
        )
        # Windows 上 git 输出正斜杠（C:/...），Path.resolve() 是反斜杠——归一化后比较
        toplevel = (
            os.path.normcase(os.path.abspath(out.stdout.strip())) if out.returncode == 0 else ""
        )
        if toplevel != os.path.normcase(self.repo_root):
            raise ValueError(f"not a git repo root: {self.repo_root}")

    def _load_session_file(self) -> None:
        """读 session 文件；缺失/指向目录不存在/非法 JSON 都清空并警告（spec F31/N5）。"""
        try:
            session = load_session(Path(self.session_file))
        except Exception as exc:  # noqa: BLE001 非法 JSON 只警告清空，不阻断启动
            print(f"worktree: session 文件损坏，已清空: {exc}", file=sys.stderr)
            clear_session(Path(self.session_file))
            return
        if session is None:
            return
        if not Path(session.worktree_path).exists():
            print("worktree: session worktree gone, cleared", file=sys.stderr)
            clear_session(Path(self.session_file))
            return
        self.current_session = session

    def _scan_worktrees(self) -> None:
        """扫描 worktree_dir 子目录还原 active 映射（纯 fs，不调 git，spec F5）。"""
        d = Path(self.worktree_dir)
        if not d.is_dir():
            return
        for p in sorted(d.iterdir()):
            if not p.is_dir() or not (p / ".git").exists():
                continue
            flat = p.name
            name = flat.replace("+", "/")
            self.active[name] = Worktree(
                name=name,
                path=str(p),
                branch=f"worktree-{flat}",
                based_on="HEAD",
                head_commit=_resolve_head_sha_from_fs(str(p)) or "",
                created=datetime.fromtimestamp(p.stat().st_mtime).astimezone(),
                manual=False,  # fs 无法判断手动与否，保守按非手动
            )

    # ── 查询 ────────────────────────────────────────────────────────────────

    def list(self) -> list[Worktree]:
        """按 name 升序返回全部 Worktree。"""
        return [self.active[n] for n in sorted(self.active)]

    def get(self, name: str) -> Worktree | None:
        return self.active.get(name)

    # 注：current_session 是实例属性（见 __init__），不使用同名方法（Python 属性遮蔽方法）
