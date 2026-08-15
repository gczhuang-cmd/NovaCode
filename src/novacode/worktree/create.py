"""Worktree 创建：create + 快速恢复 + 创建后设置 A/B/C/D（ch14 T5，spec F6-F10）。

作为 `Manager` 的 mixin（CreateMixin），经 `self` 访问 Manager 字段。
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .git import _resolve_head_sha_from_fs, _run_git
from .slug import flat_slug, validate_slug

if TYPE_CHECKING:  # 仅类型检查解析，避免模块级循环（运行时函数内再 import 构造）
    from .manager import Worktree


class CreateMixin:
    """Manager 的 create / 创建后设置方法（spec F6）。"""

    # mixin 访问 Manager 字段（由 Manager.__init__ 初始化；注解供类型检查）
    repo_root: str
    worktree_dir: str
    symlink_dirs: list[str]
    active: dict[str, Worktree]
    lock: asyncio.Lock

    async def create(self, name: str, base_ref: str, manual: bool) -> Worktree:
        """创建（或快速恢复）一个 Worktree（spec F6）。

        - `validate_slug` 不通过即抛 ValueError；
        - 目录已存在走快速恢复（纯 fs 读 SHA，不调 `git worktree add`，G5）；
        - 创建后设置 A/B/C/D best-effort，失败仅 stderr 警告（N2）。
        """
        from .manager import Worktree  # 函数内延迟导入，避免模块级循环（对齐 ch13 模式）

        validate_slug(name)
        async with self.lock:
            if name in self.active:
                raise ValueError(f"Worktree 已存在: {name}")
            flat = flat_slug(name)
            wt_path = Path(self.worktree_dir) / flat
            branch = f"worktree-{flat}"
            if wt_path.exists():  # 快速恢复（spec G5）
                head = _resolve_head_sha_from_fs(str(wt_path)) or ""
                wt = Worktree(
                    name=name,
                    path=str(wt_path),
                    branch=branch,
                    based_on=base_ref,
                    head_commit=head,
                    created=datetime.fromtimestamp(wt_path.stat().st_mtime).astimezone(),
                    manual=manual,
                )
                self.active[name] = wt
                return wt
            try:
                await _run_git(
                    self.repo_root, "worktree", "add", "-B", branch, str(wt_path), base_ref
                )
            except Exception:
                shutil.rmtree(wt_path, ignore_errors=True)
                raise
            await self._perform_post_creation_setup(wt_path)
            head_sha = await _run_git(str(wt_path), "rev-parse", "HEAD")
            wt = Worktree(
                name=name,
                path=str(wt_path),
                branch=branch,
                based_on=base_ref,
                head_commit=head_sha,
                created=datetime.now().astimezone(),
                manual=manual,
            )
            self.active[name] = wt
            return wt

    async def _perform_post_creation_setup(self, wt_path: Path) -> None:
        """创建后设置 A/B/C/D；每个子步骤失败仅 stderr 警告，不中断创建（spec N2）。"""
        steps: list[tuple[str, Callable[..., object], tuple]] = [
            ("copy_local_configs", _copy_local_configs, (self.repo_root, str(wt_path))),
            ("setup_git_hooks", _setup_git_hooks, (self.repo_root, str(wt_path))),
            (
                "symlink_large_dirs",
                _symlink_large_dirs,
                (self.repo_root, str(wt_path), self.symlink_dirs),
            ),
            ("copy_included_ignored", _copy_included_ignored, (self.repo_root, str(wt_path))),
        ]
        for step, fn, args in steps:
            try:
                await asyncio.to_thread(fn, *args)
            except Exception as exc:  # noqa: BLE001 best-effort：失败不中断创建
                print(f"worktree: setup {step}: {exc}", file=sys.stderr)


# ── 创建后设置（均为同步模块级函数，best-effort）────────────────────────────


def _copy_local_configs(repo_root: str, wt_path: str) -> None:
    """设置 A：复制本地配置文件（spec F7）。主仓有且 Worktree 无则复制。"""
    for rel in (".novacode/config.yaml", ".novacode/settings.local.yaml"):
        src = Path(repo_root) / rel
        dst = Path(wt_path) / rel
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _setup_git_hooks(repo_root: str, wt_path: str) -> None:
    """设置 B：配置 Worktree 的 core.hooksPath（spec F8）。

    优先主仓 `.husky/` 目录，回退主仓已有 `core.hooksPath` 配置；无则跳过。
    """
    husky = Path(repo_root) / ".husky"
    hooks_path: str | None = None
    if husky.is_dir():
        hooks_path = str(husky.resolve())
    else:
        out = subprocess.run(
            ["git", "-C", repo_root, "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,  # 退出码非零=无配置，属正常分支
        )
        if out.returncode == 0 and out.stdout.strip():
            hooks_path = out.stdout.strip()
    if hooks_path:
        subprocess.run(
            ["git", "-C", wt_path, "config", "core.hooksPath", hooks_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,  # best-effort：失败由上层 _perform_post_creation_setup 警告
        )


def _symlink_large_dirs(repo_root: str, wt_path: str, symlink_dirs: list[str]) -> None:
    """设置 C：软链大目录（spec F9）。主仓存在且 Worktree 缺失时创建 symlink。

    Windows 上目录 symlink 需 `target_is_directory=True`（POSIX 忽略该参数），
    无权限时抛异常由上层 best-effort 跳过。
    """
    for d in symlink_dirs:
        src = Path(repo_root) / d
        dst = Path(wt_path) / d
        if src.is_dir() and not dst.exists():
            os.symlink(src.resolve(), dst, target_is_directory=True)


def _copy_included_ignored(repo_root: str, wt_path: str) -> None:
    """设置 D：按 `.worktreeinclude` 复制被忽略但运行需要的文件（spec F10）。

    读取每行为 glob 模式（支持 `*.env`），用 `git ls-files --others --ignored
    --exclude-standard --directory` 列出忽略文件，`fnmatch` 匹配后逐个复制。
    """
    inc = Path(repo_root) / ".worktreeinclude"
    if not inc.is_file():
        return
    patterns = [
        line.strip() for line in inc.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not patterns:
        return
    out = subprocess.run(
        [
            "git",
            "-C",
            repo_root,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,  # returncode 由下方显式判断
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    for rel in out.stdout.splitlines():
        if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
            src = Path(repo_root) / rel
            dst = Path(wt_path) / rel
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
