"""Skill Executor 单测（ch11 T19）：inline/fork 分发、失败兜底、usage 回写。"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from novacode.skills import ActiveSkills, Catalog, Executor
from novacode.skills.parser import parse_skill_dir
from novacode.skills.types import SkillSource
from novacode.tool import Registry


def _console() -> Console:
    return Console(file=io.StringIO(), highlight=False, width=80)


def _skill(tmp_path, name="foo", mode="inline", fork_context="none"):
    d = tmp_path / name
    d.mkdir(parents=True)
    fm = f"name: {name}\ndescription: d\nmode: {mode}\nfork_context: {fork_context}\n"
    (d / "SKILL.md").write_text(f"---\n{fm}---\nSOP body", encoding="utf-8")
    c = Catalog()
    c.register(parse_skill_dir(d, SkillSource.USER))
    return c


class _Conv:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def add_user(self, t: str) -> None:
        self.entries.append(("user", t))

    def add_assistant(self, t: str) -> None:
        self.entries.append(("assistant", t))

    def messages(self):
        return []


class _Ctx:
    def __init__(self) -> None:
        self.conv = _Conv()
        self.console = _console()
        self.usage_box = SimpleNamespace(usage_in=0, usage_out=0)
        self.runtime = SimpleNamespace(usage_anchor=0)
        self.calls: list[str] = []

    async def run_callback(self) -> None:
        self.calls.append("cb")


class _Sub:
    """mock 子 Agent：直接写一条 assistant 文本（ch13：launch_fork 走 run_to_completion）。"""

    def __init__(
        self, provider, registry, version, engine, runtime=None, allowed_tools=None, **kwargs
    ) -> None:
        self.allowed_tools = allowed_tools
        self.runtime = runtime

    async def run_to_completion(self, conv, task="", events=None):
        conv.add_assistant("审查报告文本")
        return "审查报告文本"


def _executor(catalog, active, sub=None, monkeypatch=None) -> Executor:
    if sub is not None and monkeypatch is not None:
        # executor 函数内 `from ..agent import Agent`，故 monkeypatch agent 模块的 Agent
        monkeypatch.setattr("novacode.agent.Agent", sub)
    provider = SimpleNamespace(model="m")
    return Executor(catalog, active, Registry(), provider, None, None, "test", None)


@pytest.mark.asyncio
async def test_inline_skill_injects_and_sends(tmp_path):
    """inline：注入 user 消息 + 触发回合（F27）。"""
    catalog = _skill(tmp_path)
    active = ActiveSkills()
    ex = _executor(catalog, active)
    ctx = _Ctx()
    await ex.execute(ctx, "foo", "")
    assert ctx.conv.entries[0][0] == "user"
    assert "SOP body" in ctx.conv.entries[0][1]
    assert ctx.calls == ["cb"]


@pytest.mark.asyncio
async def test_fork_skill_writes_assistant(tmp_path, monkeypatch):
    """fork：子 Agent final_text 写回主对话（F28）。"""
    catalog = _skill(tmp_path, mode="fork", fork_context="none")
    active = ActiveSkills()
    ex = _executor(catalog, active, _Sub, monkeypatch)
    ctx = _Ctx()
    await ex.execute(ctx, "foo", "")
    assert ctx.conv.entries[-1] == ("assistant", "审查报告文本")


@pytest.mark.asyncio
async def test_fork_skill_failure_writes_failed(tmp_path, monkeypatch):
    """fork 失败 → assistant 消息为 [skill <name> failed: ...]（N7）。"""

    class _FailSub:
        def __init__(self, *a, **k) -> None:
            pass

        async def run_to_completion(self, conv, task="", events=None):
            raise RuntimeError("boom")

    catalog = _skill(tmp_path, mode="fork")
    active = ActiveSkills()
    ex = _executor(catalog, active, _FailSub, monkeypatch)
    ctx = _Ctx()
    await ex.execute(ctx, "foo", "")
    assert ctx.conv.entries[-1][0] == "assistant"
    assert "[skill foo failed:" in ctx.conv.entries[-1][1]


@pytest.mark.asyncio
async def test_fork_usage_written_back(tmp_path, monkeypatch):
    """fork usage 计入主会话 usage_box 与 usage_anchor（N6）。"""

    class _UsageSub:
        def __init__(self, *a, **k) -> None:
            pass

        async def run_to_completion(self, conv, task="", events=None):
            conv.add_assistant("x")
            if events is not None:
                await events.put(SimpleNamespace(input=100, output=50))
            return "x"

    catalog = _skill(tmp_path, mode="fork")
    active = ActiveSkills()
    ex = _executor(catalog, active, _UsageSub, monkeypatch)
    ctx = _Ctx()
    await ex.execute(ctx, "foo", "")
    assert ctx.usage_box.usage_in == 100 and ctx.usage_box.usage_out == 50
    assert ctx.runtime.usage_anchor == 100


@pytest.mark.asyncio
async def test_execute_unknown_skill(tmp_path):
    catalog = Catalog()
    active = ActiveSkills()
    ex = _executor(catalog, active)
    ctx = _Ctx()
    await ex.execute(ctx, "nope", "")
    assert ctx.conv.entries == []
    assert "skill not found" in ctx.console.file.getvalue()
