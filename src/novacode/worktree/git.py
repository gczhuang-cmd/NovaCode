"""git 子进程封装 + 变更检查 + 快速恢复 fs 读取（ch14 T3，spec F15/G5）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

# post-creation setup（spec F7/F9）复制/软链进 worktree 的顶层目录——它们在
# git status 里显示为未跟踪条目，但不是子 Agent 的真实变更，变更检查应忽略。
_SETUP_IGNORED_TOPS: tuple[str, ...] = (".novacode", ".venv", "node_modules", "vendor")


def _is_setup_entry(path: str) -> bool:
    """判断 porcelain path 是否为 setup 创建的条目（.novacode 配置 / 软链大目录）。"""
    return path.split("/", 1)[0] in _SETUP_IGNORED_TOPS


async def _run_git(work_dir: str, *args: str) -> str:
    """跑一个 git 命令，返回 stdout（rstrip 换行）；失败抛 RuntimeError(stderr)。

    统一 env：GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=""（防交互卡住），stdin 关闭。
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=work_dir,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr_b.decode("utf-8", errors="replace").strip())
    return stdout_b.decode("utf-8", errors="replace").strip()


async def _has_worktree_changes(wt_path: str, base_commit: str) -> bool:
    """Worktree 是否有未提交修改或本地多于 base 的 commit（spec F15）。

    忽略 `.novacode/` 前缀条目——post-creation setup（spec F7）会把 config.yaml /
    settings.local.yaml 复制进 worktree，这些未跟踪配置不是子 Agent 的变更。
    任一 git 命令出错 fail-closed 返回 True（宁可保留）；base_commit 为空串时
    `rev-list` 会报错 → 也返回 True（安全）。
    """
    try:
        status = await _run_git(wt_path, "status", "--porcelain")
        for line in status.splitlines():
            path = line[3:]  # porcelain 每行格式 "XY <path>"
            if _is_setup_entry(path):
                continue  # setup 复制/软链的本地配置与大目录，非子 Agent 变更
            return True
        if base_commit:
            count = await _run_git(wt_path, "rev-list", "--count", f"{base_commit}..HEAD")
            if count and count != "0":
                return True
    except Exception:  # noqa: BLE001 fail-closed
        return True
    return False


def _resolve_head_sha_from_fs(wt_path: str) -> str | None:
    """纯 fs 读还原 worktree 的 HEAD commit SHA；任何一步失败返回 None（spec G5）。

    worktree 里 `.git` 是**文件**（内容 `gitdir: <私有 gitdir>\n`），不是目录；HEAD 常为
    `ref: refs/heads/<name>`，分支 ref 实际文件在 common gitdir（读 commondir 文件，
    缺省为私有 gitdir 的父级）。解析失败返回 None，head_commit 置空由 fail-closed 兜底。
    """
    try:
        wt = Path(wt_path)
        git_file = wt / ".git"
        if not git_file.is_file():
            return None
        line = git_file.read_text(encoding="utf-8", errors="replace").strip()
        if not line.startswith("gitdir:"):
            return None
        gitdir = Path(line.split(":", 1)[1].strip())
        head = _read_text_file(gitdir / "HEAD")
        if head is None:
            return None
        if head.startswith("ref:"):
            refpath = head[4:].strip()
            sha = _read_text_file(gitdir / refpath)
            if sha is not None:
                return sha
            # 回退到 common gitdir
            common = _common_dir(gitdir)
            if common != gitdir:
                return _read_text_file(common / refpath)
            return None
        return head  # detached HEAD：直接是 SHA
    except (OSError, ValueError):
        return None


def _common_dir(gitdir: Path) -> Path:
    """worktree 私有 gitdir 的 common dir（读 commondir 文件，缺省父级）。"""
    try:
        cd_file = gitdir / "commondir"
        if cd_file.is_file():
            rel = cd_file.read_text(encoding="utf-8", errors="replace").strip()
            return (gitdir / rel).resolve()
    except OSError:
        pass
    return gitdir.parent


def _read_text_file(p: Path) -> str | None:
    """读文本文件；不存在/读失败返回 None。"""
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return None
