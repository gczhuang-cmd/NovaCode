"""enter / exit / remove / auto_cleanup 测试（ch14 T6，spec F11-F15）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from worktree_fixtures import init_repo

from novacode.worktree.lifecycle import ExitAction, ExitOptions, WorktreeHasChangesError
from novacode.worktree.manager import Manager


@pytest.fixture
def mgr(tmp_path):
    init_repo(tmp_path)
    return Manager(str(tmp_path))


def test_enter_does_not_chdir(mgr, tmp_path) -> None:
    async def go() -> tuple:
        await mgr.create("alice", "HEAD", manual=True)
        before = Path.cwd()
        session = await mgr.enter("alice")
        return session, before

    session, before = asyncio.run(go())
    assert Path.cwd() == before  # 进程 cwd 未变（spec AC9）
    assert session.worktree_path == str(tmp_path / ".novacode" / "worktrees" / "alice")
    assert session.session_id
    assert Path(mgr.session_file).is_file()  # 已持久化


def test_enter_unknown_raises(mgr) -> None:
    with pytest.raises(ValueError, match="不存在"):
        asyncio.run(mgr.enter("nope"))


def test_exit_remove_without_changes(mgr, tmp_path) -> None:
    async def go() -> tuple:
        wt = await mgr.create("alice", "HEAD", manual=True)
        await mgr.enter("alice")
        report = await mgr.exit("alice", ExitAction.REMOVE, ExitOptions())
        return wt, report

    wt, report = asyncio.run(go())
    assert report.removed
    assert not Path(wt.path).exists()
    assert mgr.current_session is None
    assert Path(mgr.session_file).read_text(encoding="utf-8").strip() == "null"


def test_exit_remove_guard_with_changes(mgr, tmp_path) -> None:
    async def go() -> Path:
        wt = await mgr.create("alice", "HEAD", manual=True)
        await mgr.enter("alice")
        (Path(wt.path) / "new.txt").write_text("x", encoding="utf-8")
        with pytest.raises(WorktreeHasChangesError):
            await mgr.exit("alice", ExitAction.REMOVE, ExitOptions())
        return Path(wt.path)

    wt_dir = asyncio.run(go())
    assert wt_dir.is_dir()  # 拒绝删除，目录仍在
    # 显式 discard 后删除成功
    asyncio.run(mgr.exit("alice", ExitAction.REMOVE, ExitOptions(discard_changes=True)))
    assert not wt_dir.exists()


def test_exit_chdir_restores_original_cwd(mgr, tmp_path, monkeypatch) -> None:
    """spec N4/G8：exit 时 os.chdir(original_cwd) 兜底——session 期间进程 cwd 被改动后恢复。"""
    original = str(Path.cwd())

    async def go() -> str:
        await mgr.create("alice", "HEAD", manual=True)
        await mgr.enter("alice")
        monkeypatch.chdir(tmp_path)  # 模拟 session 期间进程 cwd 被外部改动
        await mgr.exit("alice", ExitAction.REMOVE, ExitOptions(discard_changes=True))
        return str(Path.cwd())

    after = asyncio.run(go())
    assert after == original


def test_exit_keep(mgr, tmp_path) -> None:
    async def go() -> tuple:
        await mgr.create("alice", "HEAD", manual=True)
        await mgr.enter("alice")
        report = await mgr.exit("alice", ExitAction.KEEP, ExitOptions())
        return report, mgr.current_session

    report, sess = asyncio.run(go())
    assert not report.removed
    assert (tmp_path / ".novacode" / "worktrees" / "alice").is_dir()
    assert sess is None


def test_exit_non_current_raises(mgr) -> None:
    async def go() -> None:
        await mgr.create("alice", "HEAD", manual=True)
        await mgr.create("bob", "HEAD", manual=True)
        await mgr.enter("bob")
        await mgr.exit("alice", ExitAction.KEEP, ExitOptions())

    with pytest.raises(ValueError, match="只能退出当前"):
        asyncio.run(go())


def test_remove_non_current_session(mgr, tmp_path) -> None:
    async def go() -> Path:
        wt = await mgr.create("alice", "HEAD", manual=True)
        await mgr.create("bob", "HEAD", manual=True)
        await mgr.enter("bob")
        await mgr.remove("alice", ExitOptions(discard_changes=True))
        return Path(wt.path)

    alice_dir = asyncio.run(go())
    assert not alice_dir.exists()
    assert mgr.current_session is not None  # bob 会话不受影响


def test_auto_cleanup_branches(mgr, tmp_path) -> None:
    async def go() -> tuple:
        wt_m = await mgr.create("manual-wt", "HEAD", manual=True)
        rep1 = await mgr.auto_cleanup("manual-wt")
        wt_c = await mgr.create("agent-a1234567", "HEAD", manual=False)
        rep2 = await mgr.auto_cleanup("agent-a1234567")
        wt_d = await mgr.create("agent-a7654321", "HEAD", manual=False)
        (Path(wt_d.path) / "new.txt").write_text("x", encoding="utf-8")
        rep3 = await mgr.auto_cleanup("agent-a7654321")
        return wt_m, wt_c, wt_d, rep1, rep2, rep3

    wt_m, wt_c, wt_d, rep1, rep2, rep3 = asyncio.run(go())
    # manual=True → keep
    assert rep1.kept and rep1.path == wt_m.path
    # manual=False 无变更 → remove
    assert not rep2.kept
    assert not Path(wt_c.path).exists()
    # manual=False 有变更 → keep
    assert rep3.kept and Path(wt_d.path).is_dir()
