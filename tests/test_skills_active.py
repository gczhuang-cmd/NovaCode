"""ActiveSkills 列表单测（ch11 T7）。"""

from __future__ import annotations

from novacode.skills import ActiveSkills


def test_activate_append_and_snapshot():
    a = ActiveSkills()
    a.activate("commit", "body1")
    a.activate("review", "body2")
    snap = a.snapshot()
    assert [e.name for e in snap] == ["commit", "review"]
    assert snap[0].body == "body1"
    assert a.names() == ["commit", "review"]


def test_activate_update_keeps_position():
    a = ActiveSkills()
    a.activate("a", "1")
    a.activate("b", "2")
    a.activate("a", "updated")
    snap = a.snapshot()
    assert [e.name for e in snap] == ["a", "b"]  # 位置不变
    assert snap[0].body == "updated"


def test_clear():
    a = ActiveSkills()
    a.activate("commit", "body")
    a.clear()
    assert a.snapshot() == []
    assert a.names() == []


def test_snapshot_is_copy():
    a = ActiveSkills()
    a.activate("commit", "body")
    snap = a.snapshot()
    snap[0].body = "mutated"  # 外部改动不影响内部
    assert a.snapshot()[0].body == "body"
