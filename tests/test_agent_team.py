"""Agent Team 集成测试（ch15 T15/T16/T18/T19/T20/T32，spec F25/F41/F48-F51）。"""

from __future__ import annotations

import asyncio
import json
from typing import ClassVar

from novacode.agent import Agent, Mode
from novacode.agent.agent_tool import AgentTool
from novacode.agent.team_hook import (
    TeammateContext,
    TeamSpawnRequest,
    teammate_context_from_ctx,
    with_teammate_context,
)
from novacode.agent.team_mailbox import build_incoming_messages_reminder, ingest_team_mailbox
from novacode.llm import StreamEvent, ToolCall
from novacode.permission import new_engine
from novacode.subagent.catalog import Catalog
from novacode.subagent.definition import Definition, Source
from novacode.tool import Result, new_default_registry


def _engine(tmp_path):
    engine, err = new_engine(str(tmp_path))
    assert err is None
    return engine


class ScriptProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.call_count = 0

    async def stream(self, req):
        self.call_count += 1
        yield StreamEvent(text="done", done=True)


def _catalog(*defs: Definition) -> Catalog:
    c = Catalog()
    c._add_all(
        list(defs)
        or [
            Definition(
                name="gp",
                description="通用",
                max_turns=5,
                system_prompt="你是通用 Agent。",
                source=Source.BUILTIN,
            )
        ],
        Source.BUILTIN,
    )
    return c


class FakeTaskMgr:
    def __init__(self) -> None:
        self.launched: list[tuple] = []

    async def launch(self, ag, conv, name, task):
        self.launched.append((name, task))
        return "task_1"


# ── Agent 构造扩展（T16） ────────────────────────────────


def test_agent_exclude_and_mode_switches(tmp_path):
    ag = Agent(
        None,
        new_default_registry(),
        "test",
        _engine(tmp_path),
        exclude_tools=["TeamTaskCreate"],
        permission_mode=Mode.PLAN,
        allowed_tools=["read_file"],
    )
    assert ag._exclude_tools == ["TeamTaskCreate"]
    assert ag._permission_mode == Mode.PLAN
    ag.set_permission_mode(Mode.DEFAULT)
    ag.set_allowed_tools(["read_file", "write_file"])
    assert ag._permission_mode == Mode.DEFAULT
    assert ag._allowed_tools == ["read_file", "write_file"]


# ── TeammateContext ctx（T15） ──────────────────────────


def test_teammate_context_ctxvar(tmp_path):
    tc = TeammateContext(team_name="t", member_name="m", agent_id="a", backend_type="in-process")
    with with_teammate_context(tc):
        assert teammate_context_from_ctx(None) is tc
    assert teammate_context_from_ctx(None) is None


# ── ingest_team_mailbox（T20/T32） ──────────────────────


class _TextMsg:
    from_ = "lead"
    type = "text"
    summary = "hi"
    content = "hello world"
    timestamp = 0
    payload = None


class _PlanApproveMsg:
    from_ = "lead"
    type = "plan_approval_response"
    summary = "approved"
    content = ""
    timestamp = 0
    payload: ClassVar[dict] = {"approve": True, "feedback": ""}


class _PlanRejectMsg:
    from_ = "lead"
    type = "plan_approval_response"
    summary = "rejected"
    content = ""
    timestamp = 0
    payload: ClassVar[dict] = {"approve": False, "feedback": "太简略"}


class _FakeBox:
    def __init__(self, msgs) -> None:
        self.msgs = msgs
        self.marked = 0

    async def read_unread(self):
        return list(range(len(self.msgs))), self.msgs

    async def mark_read(self, indices):
        self.marked += len(indices)


def test_ingest_builds_reminder(tmp_path):
    box = _FakeBox([_TextMsg()])
    tc = TeammateContext(
        team_name="t",
        member_name="m",
        agent_id="a",
        backend_type="in-process",
        read_unread=box.read_unread,
        mark_read=box.mark_read,
    )
    ingest = ingest_team_mailbox(tc)

    async def go():
        reminders = await ingest()
        assert any("<incoming-messages>" in r for r in reminders)
        assert box.marked == 1

    asyncio.run(go())


def test_ingest_plan_approve_switches(tmp_path):
    box = _FakeBox([_PlanApproveMsg()])
    tc = TeammateContext(
        team_name="t",
        member_name="p",
        agent_id="a",
        backend_type="in-process",
        read_unread=box.read_unread,
        mark_read=box.mark_read,
    )
    ag = Agent(
        None,
        new_default_registry(),
        "test",
        _engine(tmp_path),
        permission_mode=Mode.PLAN,
        allowed_tools=["read_file", "TeamSendMessage"],
    )
    full = ["read_file", "write_file", "TeamSendMessage"]
    ag._pre_iteration_reminders = ingest_team_mailbox(tc, ag, full)

    async def go():
        reminders = await ag._pre_iteration_reminders()
        assert any("已批准" in r for r in reminders)

    asyncio.run(go())
    assert ag._permission_mode == Mode.DEFAULT  # 切回 default（T32/F51）
    assert ag._allowed_tools == full  # 写工具加回


def test_ingest_plan_reject_keeps_plan(tmp_path):
    box = _FakeBox([_PlanRejectMsg()])
    tc = TeammateContext(
        team_name="t",
        member_name="p",
        agent_id="a",
        backend_type="in-process",
        read_unread=box.read_unread,
        mark_read=box.mark_read,
    )
    ag = Agent(
        None,
        new_default_registry(),
        "test",
        _engine(tmp_path),
        permission_mode=Mode.PLAN,
        allowed_tools=["read_file"],
    )
    ag._pre_iteration_reminders = ingest_team_mailbox(tc, ag, [])

    async def go():
        reminders = await ag._pre_iteration_reminders()
        assert any("驳回" in r and "太简略" in r for r in reminders)

    asyncio.run(go())
    assert ag._permission_mode == Mode.PLAN  # 仍 plan


def test_build_incoming_reminder_format():
    r = build_incoming_messages_reminder([_TextMsg()])
    assert "<incoming-messages>" in r
    assert "来自 lead" in r and "hello world" in r


# ── 工具超时闭合（ch15 e2e 发现的 400 修复） ─────────────


class _SlowTool:
    """会超时的工具（只读，走并发批）。"""

    read_only = True
    is_system = False

    def name(self) -> str:
        return "slow"

    def description(self) -> str:
        return "慢工具"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(60)
        return Result(content="done")


def test_run_one_timeout_returns_error(monkeypatch, tmp_path):
    """工具超时必须转结构化错误而非抛 TimeoutError——否则批结果不完整、
    assistant tool_calls 得不到 tool_result 闭合，后续请求 400（ch15 发现）。"""
    import novacode.tool as tool_mod

    monkeypatch.setattr(tool_mod, "DEFAULT_TIMEOUT", 0.1)
    reg = new_default_registry()
    reg.register(_SlowTool())
    ag = Agent(None, reg, "test", _engine(tmp_path))
    call = ToolCall(id="c1", name="slow", input="{}")

    r = asyncio.run(ag._run_one(call))
    assert r.is_error
    assert "超时" in r.content


# ── AgentTool team 分支（T19） ──────────────────────────


class _FakeTeamHook:
    def __init__(self) -> None:
        self.called: TeamSpawnRequest | None = None

    async def spawn_teammate(self, req):
        self.called = req
        return json.dumps(
            {
                "member_name": "alice",
                "agent_id": "a1",
                "worktree": "/w",
                "backend": "in-process",
                "pane_id": "",
            }
        )

    def is_teammate_context(self, ctx):
        return "", "", False


class _BlockedHook:
    def is_teammate_context(self, ctx):
        return "t", "in-process", True


def _parent(tmp_path) -> Agent:
    return Agent(
        ScriptProvider(),
        new_default_registry(),
        "test",
        _engine(tmp_path),
        runtime=type("R", (), {"context_window": 200000})(),
    )


def test_agent_tool_team_branch(tmp_path):
    t = AgentTool(_catalog(), FakeTaskMgr(), parent=_parent(tmp_path), team_hook=_FakeTeamHook())

    async def go():
        r = await t.execute(
            json.dumps({"team_name": "demo", "prompt": "p", "description": "d", "name": "alice"})
        )
        assert not r.is_error
        assert "alice" in r.content

    asyncio.run(go())


def test_agent_tool_team_blocked_inprocess(tmp_path):
    t = AgentTool(_catalog(), FakeTaskMgr(), parent=_parent(tmp_path), team_hook=_BlockedHook())

    async def go():
        r = await t.execute(json.dumps({"team_name": "demo", "prompt": "p", "description": "d"}))
        assert r.is_error
        assert "不能嵌套" in r.content

    asyncio.run(go())


def test_agent_tool_no_team_hook(tmp_path):
    t = AgentTool(_catalog(), FakeTaskMgr(), parent=_parent(tmp_path))

    async def go():
        r = await t.execute(json.dumps({"team_name": "demo", "prompt": "p", "description": "d"}))
        assert r.is_error
        assert "未启用" in r.content

    asyncio.run(go())


# ── spawn_teammate in-process 全流程（T18） ─────────────


def test_spawn_inprocess_member(tmp_path, monkeypatch):
    from worktree_fixtures import init_repo

    init_repo(tmp_path)
    from novacode.worktree import Manager as WtManager

    wt = WtManager(str(tmp_path))
    from novacode.team import Manager as TeamManager
    from novacode.team.registry import AgentNameRegistry

    home = tmp_path / "home"
    name_reg = AgentNameRegistry()
    tmgr = TeamManager(str(home), wt, FakeTaskMgr(), name_reg)
    monkeypatch.setenv("NOVA_SANDBOX", "1")  # 无实际作用，仅占位避免误用

    async def go():
        await tmgr.create("demo")
        req = TeamSpawnRequest(
            team_name="demo",
            member_name="alice",
            agent_type="gp",
            prompt="写报告",
            parent=_parent(tmp_path),
            catalog=_catalog(
                Definition(
                    name="gp",
                    description="通用",
                    max_turns=5,
                    system_prompt="你是通用 Agent。",
                    source=Source.BUILTIN,
                )
            ),
        )
        text = await tmgr.spawn_teammate(req)
        data = json.loads(text)
        assert data["member_name"] == "alice"
        assert data["backend"] == "in-process"
        team = tmgr.get("demo")
        assert team is not None
        m = team.member_by_name("alice")
        assert m is not None and m.worktree_path
        # 队员 worktree 落地 + 持久化到 config
        assert (tmp_path / ".novacode" / "worktrees" / "team-demo+alice").is_dir()
        from novacode.team.persistence import read_json

        assert read_json(team.config_path)["members"][-1]["name"] == "alice"
        assert name_reg.resolve("alice") == "task_1"  # 注册到 AgentNameRegistry

    asyncio.run(go())
