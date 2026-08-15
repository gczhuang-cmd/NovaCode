"""Team 工具测试（ch15 T22，spec F20-F34）。"""

from __future__ import annotations

import asyncio
import json

from novacode.team.manager import Manager
from novacode.team.persistence import read_json
from novacode.team.registry import AgentNameRegistry
from novacode.team.tools import (
    TeamCreateTool,
    TeamDeleteTool,
    TeamSendMessageTool,
    TeamTaskCreateTool,
    TeamTaskGetTool,
    TeamTaskListTool,
    TeamTaskUpdateTool,
)
from novacode.team.types import Team, TeammateInfo


def _no_backend(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr("novacode.team.backend.detect.shutil.which", lambda name: None)


def _mgr(tmp_path, monkeypatch):
    _no_backend(monkeypatch)
    mgr = Manager(str(tmp_path))
    mgr.registry = AgentNameRegistry()
    return mgr


def _run(coro):
    return asyncio.run(coro)


def test_team_create(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    r = _run(TeamCreateTool(mgr).execute(json.dumps({"team_name": "demo", "description": "d"})))
    data = json.loads(r.content)
    assert data["team_name"] == "demo"
    assert data["backend"] == "in-process"
    assert mgr.get("demo") is not None


def test_team_delete_not_found(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    r = _run(TeamDeleteTool(mgr).execute(json.dumps({"team_name": "nope"})))
    assert r.is_error and "未找到" in r.content


def test_task_tools_full_flow(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _run(TeamCreateTool(mgr).execute(json.dumps({"team_name": "demo"})))
    r = _run(TeamTaskCreateTool(mgr).execute(json.dumps({"title": "t1", "assignee": "alice"})))
    tid = json.loads(r.content)["task_id"]
    lst = json.loads(_run(TeamTaskListTool(mgr).execute("{}")).content)
    assert len(lst) == 1 and lst[0]["title"] == "t1"
    got = json.loads(_run(TeamTaskGetTool(mgr).execute(json.dumps({"task_id": tid}))).content)
    assert got["assignee"] == "alice"
    upd = json.loads(
        _run(
            TeamTaskUpdateTool(mgr).execute(json.dumps({"task_id": tid, "status": "completed"}))
        ).content
    )
    assert upd["status"] == "completed"


def test_task_blocked_by_bidirectional(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _run(TeamCreateTool(mgr).execute(json.dumps({"team_name": "demo"})))
    a = json.loads(_run(TeamTaskCreateTool(mgr).execute(json.dumps({"title": "a"}))).content)[
        "task_id"
    ]
    b = json.loads(_run(TeamTaskCreateTool(mgr).execute(json.dumps({"title": "b"}))).content)[
        "task_id"
    ]
    _run(TeamTaskUpdateTool(mgr).execute(json.dumps({"task_id": b, "add_blocked_by": [a]})))
    ta = json.loads(_run(TeamTaskGetTool(mgr).execute(json.dumps({"task_id": a}))).content)
    tb = json.loads(_run(TeamTaskGetTool(mgr).execute(json.dumps({"task_id": b}))).content)
    assert a in tb["blocked_by"]
    assert b in ta["blocks"]


def test_send_message_delivers(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)

    async def go():
        t = await mgr.create("demo")
        await t.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=False))
        mgr.registry.register("alice", "a1")
        r = await TeamSendMessageTool(mgr).execute(
            json.dumps({"to": "alice", "summary": "hi", "message": "hello"})
        )
        data = json.loads(r.content)
        assert data["delivered_to"] == ["a1"]
        from novacode.team.mailbox import Box

        msgs = await Box(t.mailbox_dir).read("a1")
        assert len(msgs) == 1 and msgs[0].content == "hello"

    asyncio.run(go())


def test_send_message_broadcast(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)

    async def go():
        t = await mgr.create("demo")
        await t.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=False))
        await t.add_member(TeammateInfo(name="bob", agent_id="b1", is_active=False))
        mgr.registry.register("alice", "a1")
        mgr.registry.register("bob", "b1")
        r = await TeamSendMessageTool(mgr).execute(
            json.dumps({"to": "*", "summary": "all", "message": "ping"})
        )
        data = json.loads(r.content)
        assert set(data["delivered_to"]) == {"a1", "b1"}
        from novacode.team.mailbox import Box

        box = Box(t.mailbox_dir)
        assert len(await box.read("a1")) == 1
        assert len(await box.read("b1")) == 1

    asyncio.run(go())


class _ResumeTaskMgr:
    """mock task.Manager：status=CANCELLED，记录 send_message 续派。"""

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def get(self, task_id):
        from novacode.task.manager import Status

        class _BT:
            status = Status.CANCELLED

        return _BT()

    async def send_message(self, name, message):
        self.sent.append((name, message))
        return "task_1"


def test_send_message_resumes_inprocess(tmp_path, monkeypatch):
    """in-process 目标已 stop 时，TeamSendMessage 触发 task_mgr.send_message 续派（F46/T31）。"""
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.task_mgr = _ResumeTaskMgr()

    async def go():
        t = await mgr.create("demo")
        await t.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=False))
        mgr.registry.register("alice", "a1")
        r = await TeamSendMessageTool(mgr).execute(
            json.dumps({"to": "alice", "summary": "hi", "message": "go2"})
        )
        assert not r.is_error
        assert mgr.task_mgr.sent == [("alice", "go2")]  # 续派调用
        assert (
            Team.from_dict(read_json(t.config_path)).member_by_name("alice").is_active is True
        )  # 续派前标 active

    asyncio.run(go())


def test_send_message_no_team(tmp_path, monkeypatch):
    """无活跃 Team 时 TeamSendMessage 抛错兜底（失败回归）。"""
    mgr = _mgr(tmp_path, monkeypatch)
    r = _run(
        TeamSendMessageTool(mgr).execute(json.dumps({"to": "x", "summary": "s", "message": "m"}))
    )
    assert r.is_error and ("没有活跃 Team" in r.content or "未找到" in r.content)


def test_plan_approval_only_lead(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)

    async def go():
        t = await mgr.create("demo")
        await t.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=True))
        mgr.registry.register("alice", "a1")
        # 队员 ctx（TeammateContext）下发 plan_approval_response 被拒
        from novacode.agent.team_hook import TeammateContext, with_teammate_context

        tc = TeammateContext(
            team_name="demo", member_name="alice", agent_id="a1", backend_type="in-process"
        )
        with with_teammate_context(tc):
            r = await TeamSendMessageTool(mgr).execute(
                json.dumps(
                    {"to": "lead", "type": "plan_approval_response", "payload": {"approve": True}}
                )
            )
            assert r.is_error and "仅 Lead" in r.content

    asyncio.run(go())
