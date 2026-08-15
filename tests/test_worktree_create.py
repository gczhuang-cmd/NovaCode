"""create + 快速恢复 + 创建后设置 A/B/C/D 测试（ch14 T5，spec F6-F10）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from worktree_fixtures import git, init_repo

from novacode.worktree.manager import Manager


def _can_symlink(tmp_path) -> bool:
    """检测当前环境是否可创建目录 symlink（Windows 无开发者模式/管理员权限时不可用）。"""
    src = tmp_path / "_src"
    dst = tmp_path / "_dst"
    src.mkdir()
    try:
        os.symlink(src, dst, target_is_directory=True)
        return True
    except OSError:
        return False


@pytest.fixture
def mgr(tmp_path):
    init_repo(tmp_path)
    return Manager(str(tmp_path))


def test_create_lands_worktree(mgr, tmp_path) -> None:
    wt = asyncio.run(mgr.create("alice", "HEAD", manual=True))
    assert wt.name == "alice"
    assert wt.branch == "worktree-alice"
    assert wt.based_on == "HEAD"
    wt_dir = Path(mgr.worktree_dir) / "alice"
    assert wt_dir.is_dir()
    assert (wt_dir / "README.md").is_file()
    assert wt.head_commit
    assert git("branch", "--show-current", cwd=wt_dir) == "worktree-alice"


def test_create_nested_flat_slug(mgr) -> None:
    wt = asyncio.run(mgr.create("team/alice", "HEAD", manual=True))
    assert wt.branch == "worktree-team+alice"
    assert Path(mgr.worktree_dir) / "team+alice" == Path(wt.path)
    assert (Path(wt.path)).is_dir()


def test_create_invalid_slug_raises(mgr) -> None:
    with pytest.raises(ValueError):
        asyncio.run(mgr.create("../etc", "HEAD", manual=True))


def test_create_duplicate_raises(mgr) -> None:
    async def go() -> None:
        await mgr.create("alice", "HEAD", manual=True)
        await mgr.create("alice", "HEAD", manual=True)

    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(go())


def test_create_fast_recovery_no_git_call(mgr, monkeypatch) -> None:
    import novacode.worktree.create as create_mod

    fake_calls: list[tuple] = []
    real = create_mod._run_git

    async def fake_run_git(work_dir, *args):
        fake_calls.append(args)
        return await real(work_dir, *args)

    monkeypatch.setattr(create_mod, "_run_git", fake_run_git)

    async def go() -> tuple:
        wt1 = await mgr.create("alice", "HEAD", manual=True)
        # 模拟进程重启/active 丢失但目录仍在磁盘：从 active 摘除后再次 create
        del mgr.active["alice"]
        calls_after_first = len(fake_calls)
        wt2 = await mgr.create("alice", "HEAD", manual=True)
        return wt1, wt2, calls_after_first

    wt1, wt2, calls_after_first = asyncio.run(go())
    # 第二次 create 走快速恢复（spec F6 步骤 4）：不再发起任何 git 子进程
    assert len(fake_calls) == calls_after_first
    assert wt1.head_commit == wt2.head_commit


def test_setup_A_copies_settings(tmp_path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".novacode").mkdir()
    (tmp_path / ".novacode" / "settings.local.yaml").write_text("permissions:\n", encoding="utf-8")
    mgr = Manager(str(tmp_path))
    asyncio.run(mgr.create("alice", "HEAD", manual=True))
    dst = tmp_path / ".novacode" / "worktrees" / "alice" / ".novacode" / "settings.local.yaml"
    assert dst.is_file()


def test_setup_B_hooks_path(tmp_path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".husky").mkdir()
    (tmp_path / ".husky" / "pre-commit").write_text("", encoding="utf-8")
    mgr = Manager(str(tmp_path))
    asyncio.run(mgr.create("alice", "HEAD", manual=True))
    wt_dir = tmp_path / ".novacode" / "worktrees" / "alice"
    hp = git("config", "--get", "core.hooksPath", cwd=wt_dir)
    assert hp == str((tmp_path / ".husky").resolve())


def test_setup_C_symlink_node_modules(tmp_path) -> None:
    if not _can_symlink(tmp_path):
        pytest.skip("当前环境无法创建目录 symlink（Windows 需开发者模式/管理员权限）")
    init_repo(tmp_path)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg").write_text("", encoding="utf-8")
    mgr = Manager(str(tmp_path))
    asyncio.run(mgr.create("alice", "HEAD", manual=True))
    link = tmp_path / ".novacode" / "worktrees" / "alice" / "node_modules"
    assert link.is_symlink()
    assert (link / "pkg").is_file()  # 软链指向主仓 node_modules


def test_setup_D_copies_worktreeinclude(tmp_path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".worktreeinclude").write_text("*.env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")  # .env 被忽略
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "gitignore", cwd=tmp_path)
    mgr = Manager(str(tmp_path))
    asyncio.run(mgr.create("alice", "HEAD", manual=True))
    dst = tmp_path / ".novacode" / "worktrees" / "alice" / ".env"
    assert dst.is_file()
    assert dst.read_text(encoding="utf-8") == "SECRET=1\n"
