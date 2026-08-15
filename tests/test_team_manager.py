"""Team 数据层测试（ch15 T1-T4，spec F1-F10/F63/F19c）。"""

from __future__ import annotations

import asyncio

from novacode.team.manager import Manager
from novacode.team.persistence import atomic_write_json, read_json, sanitize
from novacode.team.types import (
    BackendType,
    Team,
    TeamHasActiveMembersError,
    TeammateInfo,
)


def test_sanitize():
    assert sanitize("foo bar/baz") == "foo-bar-baz"
    assert sanitize("  refactor auth  ") == "refactor-auth"
    assert sanitize("!!!") == ""


def test_atomic_write_and_read(tmp_path):
    p = tmp_path / "cfg.json"
    atomic_write_json(p, {"a": 1})
    assert read_json(p) == {"a": 1}


def test_team_roundtrip():
    t = Team(
        name="foo bar",
        sanitized_name="foo-bar",
        lead_agent_id="lead",
        backend=BackendType.IN_PROCESS,
    )
    t.members = [TeammateInfo(name="lead", agent_id="lead", is_active=None)]
    d = t.to_dict()
    assert d["members"][0]["is_active"] is None  # None 语义保留
    t2 = Team.from_dict(d)
    assert t2.sanitized_name == "foo-bar"
    assert t2.members[0].name == "lead"
    assert t2.members[0].is_active is None


def _no_backend(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr("novacode.team.backend.detect.shutil.which", lambda name: None)


def test_manager_create_sanitize_suffix(tmp_path, monkeypatch):
    _no_backend(monkeypatch)
    mgr = Manager(str(tmp_path))

    async def go():
        t1 = await mgr.create("foo bar/baz")
        assert t1.sanitized_name == "foo-bar-baz"
        assert t1.backend == BackendType.IN_PROCESS
        assert t1.members[0].name == "lead"
        assert (tmp_path / ".novacode" / "teams" / "foo-bar-baz" / "config.json").is_file()
        # 同名 → -2 后缀
        t2 = await mgr.create("foo bar/baz")
        assert t2.sanitized_name == "foo-bar-baz-2"
        assert (tmp_path / ".novacode" / "teams" / "foo-bar-baz-2").is_dir()
        # 重启扫描还原
        mgr2 = Manager(str(tmp_path))
        assert mgr2.get("foo-bar-baz") is not None
        assert mgr2.get("foo-bar-baz-2") is not None

    asyncio.run(go())


def test_manager_delete_active_blocked(tmp_path, monkeypatch):
    _no_backend(monkeypatch)
    mgr = Manager(str(tmp_path))

    async def go():
        t = await mgr.create("demo")
        await t.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=True))
        try:
            await mgr.delete("demo", False)
            assert False, "should raise TeamHasActiveMembersError"
        except TeamHasActiveMembersError:
            pass
        assert mgr.get("demo") is not None  # 目录仍在
        await mgr.delete("demo", True)
        assert mgr.get("demo") is None
        assert not (tmp_path / ".novacode" / "teams" / "demo").exists()

    asyncio.run(go())


def test_member_ops_persist(tmp_path, monkeypatch):
    _no_backend(monkeypatch)
    mgr = Manager(str(tmp_path))

    async def go():
        t = await mgr.create("demo")
        await t.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=True))
        await t.set_member_active("alice", False)
        t2 = Team.from_dict(read_json(t.config_path))
        assert t2.member_by_name("alice").is_active is False
        await t.remove_member("alice")
        assert Team.from_dict(read_json(t.config_path)).member_by_name("alice") is None

    asyncio.run(go())


def test_reload_from_disk_locked(tmp_path, monkeypatch):
    """跨进程并发兜底（T3b）：set_member_active 前先 reload disk，不静默 no-op。"""
    _no_backend(monkeypatch)
    mgr = Manager(str(tmp_path))

    async def go():
        t = await mgr.create("demo")
        # 模拟子进程先写了 alice 到 disk（内存 Team 未 reload，看不到 alice）
        data = read_json(t.config_path)
        data["members"].append(TeammateInfo(name="alice", agent_id="a1", is_active=False).to_dict())
        atomic_write_json(t.config_path, data)
        # 走 reload 路径：应找到 alice 并标记，而非抛 MemberNotFound / no-op
        await t.set_member_active("alice", True)
        assert t.member_by_name("alice") is not None
        assert Team.from_dict(read_json(t.config_path)).member_by_name("alice").is_active is True

    asyncio.run(go())


def test_broken_config_skipped(tmp_path, monkeypatch):
    _no_backend(monkeypatch)
    d = tmp_path / ".novacode" / "teams" / "broken"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{not valid json", encoding="utf-8")
    mgr = Manager(str(tmp_path))  # 只警告跳过，不抛
    assert mgr.get("broken") is None


def test_manager_create_disk_fail(monkeypatch, tmp_path):
    """创建 Team 时 disk 写失败 → 抛错，内存 teams 不留半成品（失败回归）。"""
    _no_backend(monkeypatch)

    def _boom(path, value):
        raise OSError("disk full")

    # manager.py 已 `from .persistence import atomic_write_json` 绑定到自身命名空间
    monkeypatch.setattr("novacode.team.manager.atomic_write_json", _boom)
    mgr = Manager(str(tmp_path))
    try:
        asyncio.run(mgr.create("demo"))
        assert False, "should raise OSError"
    except OSError:
        pass
    assert mgr.get("demo") is None  # 内存不留半成品
    assert mgr.list_() == []
