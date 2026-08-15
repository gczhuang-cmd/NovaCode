"""AgentNameRegistry 测试（ch15 T7，spec F35-F38）。"""

from __future__ import annotations

from novacode.team.registry import AgentNameRegistry


def test_register_resolve_name_of():
    r = AgentNameRegistry()
    r.register("alice", "a1")
    assert r.resolve("alice") == "a1"
    assert r.resolve("a1") == "a1"  # agent_id 反查
    assert r.name_of("a1") == "alice"


def test_overwrite_name():
    r = AgentNameRegistry()
    r.register("alice", "a1")
    r.register("alice", "a2")
    assert r.resolve("alice") == "a2"
    assert r.name_of("a1") is None  # 旧映射清除
    assert r.name_of("a2") == "alice"


def test_same_id_two_names():
    r = AgentNameRegistry()
    r.register("alice", "a1")
    r.register("bob", "a1")
    assert r.name_of("a1") == "bob"  # 后注册覆盖
    assert r.resolve("alice") is None


def test_unregister():
    r = AgentNameRegistry()
    r.register("alice", "a1")
    r.unregister("alice")
    assert r.resolve("alice") is None
    r.register("bob", "a1")
    r.unregister_by_agent_id("a1")
    assert r.resolve("bob") is None
