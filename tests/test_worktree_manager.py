"""Manager 构造 + session 持久化测试（ch14 T4，spec F5/F31/N5）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from worktree_fixtures import init_repo

from novacode.worktree.manager import Manager
from novacode.worktree.session import WorktreeSession, save_session


def test_construct_rejects_non_git(tmp_path) -> None:
    (tmp_path / "notgit").mkdir()
    with pytest.raises(ValueError, match="not a git repo root"):
        Manager(str(tmp_path / "notgit"))


def test_construct_creates_worktree_dir(tmp_path) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))
    assert Path(mgr.worktree_dir).is_dir()
    assert mgr.current_session is None
    assert mgr.list() == []


def test_loads_existing_session(tmp_path) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))
    wt_dir = Path(mgr.worktree_dir) / "alice"
    wt_dir.mkdir(parents=True, exist_ok=True)
    sess = WorktreeSession(
        original_cwd=str(tmp_path),
        worktree_path=str(wt_dir),
        worktree_name="alice",
        original_branch="main",
        original_head_commit="abc",
        session_id="s1",
    )
    save_session(Path(mgr.session_file), sess)
    mgr2 = Manager(str(tmp_path))
    assert mgr2.current_session is not None
    assert mgr2.current_session.worktree_name == "alice"


def test_clears_session_when_worktree_gone(tmp_path, capsys) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))
    sess = WorktreeSession(
        original_cwd=str(tmp_path),
        worktree_path=str(tmp_path / ".novacode" / "worktrees" / "gone"),
        worktree_name="gone",
        original_branch="main",
        original_head_commit="abc",
        session_id="s1",
    )
    save_session(Path(mgr.session_file), sess)
    mgr2 = Manager(str(tmp_path))
    assert mgr2.current_session is None
    assert Path(mgr2.session_file).read_text(encoding="utf-8").strip() == "null"
    assert "gone" in capsys.readouterr().err


def test_clears_session_on_invalid_json(tmp_path, capsys) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))
    Path(mgr.session_file).write_text("{not json", encoding="utf-8")
    mgr2 = Manager(str(tmp_path))
    assert mgr2.current_session is None
    assert Path(mgr2.session_file).read_text(encoding="utf-8").strip() == "null"
    assert "损坏" in capsys.readouterr().err


def test_scan_restores_active(tmp_path) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))

    async def create() -> None:
        await mgr.create("team/alice", "HEAD", manual=True)

    import asyncio

    asyncio.run(create())
    # 重建 Manager：从文件系统扫描还原 active（纯 fs）
    mgr2 = Manager(str(tmp_path))
    wt = mgr2.get("team/alice")
    assert wt is not None
    assert wt.branch == "worktree-team+alice"
