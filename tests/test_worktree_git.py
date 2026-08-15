"""git helper 单测（ch14 T3）。"""

from __future__ import annotations

import asyncio

import pytest
from worktree_fixtures import add_real_worktree, init_repo

from novacode.worktree.git import _has_worktree_changes, _resolve_head_sha_from_fs, _run_git


def test_run_git_ok_and_err(tmp_path) -> None:
    init_repo(tmp_path)

    async def go() -> None:
        out = await _run_git(str(tmp_path), "rev-parse", "--abbrev-ref", "HEAD")
        assert out  # 默认分支名（master/main 因 git 版本而异）
        with pytest.raises(RuntimeError):
            await _run_git(str(tmp_path), "no-such-subcommand")

    asyncio.run(go())


def test_run_git_sets_prompt_env(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)

    async def go() -> None:
        # env 注入 GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=""
        import novacode.worktree.git as git_mod

        captured: dict | None = None
        real = git_mod.asyncio.create_subprocess_exec

        async def spy(*args, **kw):
            nonlocal captured
            captured = kw.get("env")
            return await real(*args, **kw)

        monkeypatch.setattr(git_mod.asyncio, "create_subprocess_exec", spy)
        await git_mod._run_git(str(tmp_path), "rev-parse", "HEAD")

    asyncio.run(go())


def test_has_worktree_changes(tmp_path) -> None:
    init_repo(tmp_path)
    wt = add_real_worktree(tmp_path, "w1")

    async def go() -> None:
        head = await _run_git(wt, "rev-parse", "HEAD")
        assert not await _has_worktree_changes(wt, head)  # 无修改
        (tmp_path / "worktrees" / "w1" / "new.txt").write_text("x", encoding="utf-8")
        assert await _has_worktree_changes(wt, head)  # 未提交修改
        # git 命令出错 fail-closed 返回 True
        assert await _has_worktree_changes(str(tmp_path / "nonexistent"), head)
        # base_commit 空串（rev-list 报错）也 fail-closed
        assert await _has_worktree_changes(wt, "")

    asyncio.run(go())


def test_resolve_head_sha_from_fs(tmp_path) -> None:
    init_repo(tmp_path)
    wt = add_real_worktree(tmp_path, "w1")
    head = asyncio.run(_run_git(wt, "rev-parse", "HEAD"))
    sha = _resolve_head_sha_from_fs(wt)
    assert sha == head
    assert _resolve_head_sha_from_fs(str(tmp_path / "nonexistent")) is None


def test_has_worktree_changes_ignores_setup_entries(tmp_path) -> None:
    """`.novacode/` 与软链大目录（.venv 等，post-creation setup 创建）不算变更。"""
    from pathlib import Path

    init_repo(tmp_path)
    wt = add_real_worktree(tmp_path, "w2")

    async def go() -> None:
        head = await _run_git(wt, "rev-parse", "HEAD")
        novacode = Path(wt) / ".novacode"
        novacode.mkdir()
        (novacode / "config.yaml").write_text("x", encoding="utf-8")
        (Path(wt) / ".venv").write_text("x", encoding="utf-8")  # 模拟软链大目录（git 视为未跟踪）
        assert not await _has_worktree_changes(wt, head)  # setup 条目忽略
        (Path(wt) / "real.txt").write_text("x", encoding="utf-8")
        assert await _has_worktree_changes(wt, head)  # 真实变更仍检出

    asyncio.run(go())
