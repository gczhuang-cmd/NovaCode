"""sweep_stale 测试（ch14 T7，spec F33）。"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from worktree_fixtures import git, init_repo

from novacode.worktree.manager import Manager
from novacode.worktree.sweep import EPHEMERAL_PATTERN, random_agent_name


def test_random_agent_name_matches_pattern() -> None:
    name = random_agent_name()
    assert EPHEMERAL_PATTERN.fullmatch(name)


def _age(p: Path, seconds: float = 3600) -> None:
    old = time.time() - seconds
    os.utime(p, (old, old))


def test_sweep_only_clean_ephemeral(tmp_path) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))

    async def go() -> tuple:
        wt1 = await mgr.create("agent-a1234567", "HEAD", manual=False)  # 匹配、无变更 → 删
        wt2 = await mgr.create("agent-a7654321", "HEAD", manual=False)  # 匹配、有变更 → 保留
        (Path(wt2.path) / "new.txt").write_text("x", encoding="utf-8")
        wt3 = await mgr.create("mymanual", "HEAD", manual=True)  # 不匹配 → 跳过
        for p in (Path(wt1.path), Path(wt2.path), Path(wt3.path)):
            _age(p)
        past = datetime.now().astimezone() - timedelta(minutes=30)
        removed = await mgr.sweep_stale(past)
        return wt1, wt2, wt3, removed

    wt1, wt2, wt3, removed = asyncio.run(go())
    assert removed == ["agent-a1234567"]
    assert not Path(wt1.path).exists()
    assert Path(wt2.path).is_dir()
    assert Path(wt3.path).is_dir()


def test_sweep_skips_current_session(tmp_path) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))

    async def go() -> list:
        wt = await mgr.create("agent-a1234567", "HEAD", manual=False)
        await mgr.enter(wt.name)
        _age(Path(wt.path))
        past = datetime.now().astimezone() - timedelta(minutes=30)
        return await mgr.sweep_stale(past)

    removed = asyncio.run(go())
    assert removed == []


def test_sweep_skips_unpushed_commit(tmp_path) -> None:
    """spec F33：有未推送 commit 的 worktree 目录保留（fail-closed）。"""
    init_repo(tmp_path)
    bare = tmp_path / "remote.git"
    git("init", "--bare", str(bare), cwd=tmp_path)  # bare 目录尚不存在，不能作 cwd
    git("remote", "add", "origin", str(bare), cwd=tmp_path)
    git("push", "-q", "origin", "HEAD", cwd=tmp_path)
    mgr = Manager(str(tmp_path))

    async def go() -> list:
        wt = await mgr.create("agent-a1234567", "HEAD", manual=False)
        # worktree 分支上新增一个 commit（未推送）
        git("commit", "--allow-empty", "-q", "-m", "unpushed", cwd=Path(wt.path))
        _age(Path(wt.path))
        past = datetime.now().astimezone() - timedelta(minutes=30)
        return await mgr.sweep_stale(past)

    removed = asyncio.run(go())
    assert removed == []  # 有未推送 commit → 保留


def test_sweep_skips_fresh_dirs(tmp_path) -> None:
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))

    async def go() -> list:
        await mgr.create("agent-a1234567", "HEAD", manual=False)
        # 目录 mtime 是 now（未来 cutoff 内）→ 第一层不删
        past = datetime.now().astimezone() - timedelta(days=1)
        removed = await mgr.sweep_stale(past)
        return removed

    removed = asyncio.run(go())
    assert removed == []
