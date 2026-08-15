"""命令注册中心单测：注册、冲突、前缀匹配、visible 排序（ch10 T2）。"""

from __future__ import annotations

import pytest

from novacode.command.command import Command, Kind
from novacode.command.registry import Registry


def _cmd(name: str, **kw) -> Command:
    async def _handler(ctx):
        pass

    return Command(name=name, description=f"{name} 描述", kind=Kind.LOCAL, handler=_handler, **kw)


def test_register_ok():
    reg = Registry()
    reg.register(_cmd("help"))
    assert reg.lookup("help") is not None
    assert reg.lookup("HELP").name == "help"  # lookup 内部小写化


def test_register_duplicate_name_raises():
    reg = Registry()
    reg.register(_cmd("help"))
    with pytest.raises(RuntimeError, match="help"):
        reg.register(_cmd("help"))


def test_register_duplicate_alias_raises():
    reg = Registry()
    reg.register(_cmd("help", aliases=["h"]))
    with pytest.raises(RuntimeError, match="h"):
        reg.register(_cmd("status", aliases=["h"]))


def test_register_alias_conflicts_with_name():
    reg = Registry()
    reg.register(_cmd("help"))
    with pytest.raises(RuntimeError, match="help"):
        reg.register(_cmd("h", aliases=["help"]))


def test_register_uppercase_name_rejected():
    reg = Registry()
    with pytest.raises(RuntimeError, match="lowercase"):
        reg.register(_cmd("Help"))


def test_visible_sorted():
    reg = Registry()
    for n in ("resume", "exit", "clear"):
        reg.register(_cmd(n))
    assert [c.name for c in reg.visible()] == ["clear", "exit", "resume"]


def test_visible_excludes_hidden():
    reg = Registry()
    reg.register(_cmd("help"))
    reg.register(_cmd("secret", hidden=True))
    assert [c.name for c in reg.visible()] == ["help"]
    assert reg.lookup("secret") is not None  # hidden 仍可命中（F28）


def test_prefix_match():
    reg = Registry()
    for n in ("status", "session", "help", "review"):
        reg.register(_cmd(n))
    assert [c.name for c in reg.prefix_match("/s")] == ["session", "status"]
    assert [c.name for c in reg.prefix_match("")] == [c.name for c in reg.visible()]
    assert [c.name for c in reg.prefix_match("/xx")] == []


def test_prefix_match_ignores_alias_and_description():
    """F25：只按主名前缀匹配，不含别名/描述。"""
    reg = Registry()
    reg.register(_cmd("status", aliases=["st"]))
    reg.register(_cmd("exit", aliases=["q"]))
    # "q" 不是任何命令名前缀 → 空；若别名参与匹配则会命中 exit
    assert [c.name for c in reg.prefix_match("/q")] == []
