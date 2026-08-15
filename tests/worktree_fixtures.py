"""ch14 worktree 测试共享工具：临时 git 仓库 + 真实 worktree 准备（ch13 风格独立模块）。"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(*args: str, cwd: Path) -> str:
    """跑 git 命令并返回 stdout（rstrip）；失败抛 RuntimeError。"""
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,  # returncode 由下方显式判断
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def init_repo(root: Path, *, commit: bool = True) -> None:
    """初始化一个带一次提交的 git 仓库（测试 fixture 根目录）。"""
    root.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=root)
    git("config", "user.email", "test@example.com", cwd=root)
    git("config", "user.name", "test", cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    if commit:
        git("add", ".", cwd=root)
        git("commit", "-q", "-m", "init", cwd=root)


def add_real_worktree(repo: Path, name: str) -> str:
    """用 `git worktree add` 准备一个真实 worktree，返回 worktree 绝对路径。

    目录落在 `<repo>/worktrees/<name>`（与 Manager 的 `.novacode/worktrees/` 无关，
    仅用于测试 git.py 对真实 worktree 的 fs/git 读取）。
    """
    branch = f"worktree-{name}"
    wt = repo / "worktrees" / name
    wt.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-B", branch, str(wt), "HEAD", cwd=repo)
    return str(wt)
