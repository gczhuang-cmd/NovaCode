"""Agent 工具测试（ch13 T18）。"""

from __future__ import annotations

import asyncio
import json

from novacode.agent import Agent
from novacode.agent.agent_tool import AgentTool
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent
from novacode.permission import new_engine
from novacode.subagent.catalog import Catalog
from novacode.subagent.definition import Definition, Source
from novacode.tool import new_default_registry


def _engine(tmp_path):
    engine, err = new_engine(str(tmp_path))
    assert err is None
    return engine


class ScriptProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.call_count = 0
        self.requests: list[Request] = []

    async def stream(self, req: Request):
        self.call_count += 1
        self.requests.append(req)
        if not self.scripts:
            await asyncio.sleep(3600)  # 无脚本：挂起（超时切后台测试用）
            return
        idx = min(self.call_count - 1, len(self.scripts) - 1)
        for ev in self.scripts[idx]:
            yield ev


class FakeTaskMgr:
    """mock task.Manager：记录 launch / adopt_running 调用。"""

    def __init__(self) -> None:
        self.launched: list[tuple] = []
        self.adopted: list[tuple] = []
        self._next = 0

    async def launch(self, ag, conv, name, task):
        self._next += 1
        task_id = f"task_{self._next}"
        self.launched.append((ag, conv, name, task))
        return task_id

    async def adopt_running(self, ag, conv, name, events, handle, partial):
        self._next += 1
        task_id = f"task_{self._next}"
        self.adopted.append((ag, conv, name, events, handle, partial))
        return task_id


def _simple_catalog() -> Catalog:
    c = Catalog()
    c._add_all(
        [
            Definition(
                name="Explore",
                description="只读探索",
                disallowed_tools=["write_file", "edit_file"],
                max_turns=5,
                system_prompt="你是探索 Agent。",
                source=Source.BUILTIN,
            )
        ],
        Source.BUILTIN,
    )
    return c


def _isolation_catalog() -> Catalog:
    """含 isolation:worktree 定义的目录（ch14）。"""
    c = Catalog()
    c._add_all(
        [
            Definition(
                name="wt",
                description="worktree 隔离",
                isolation="worktree",
                dont_ask=True,
                max_turns=5,
                system_prompt="你是隔离 Agent。",
                source=Source.BUILTIN,
            )
        ],
        Source.BUILTIN,
    )
    return c


def _text(text: str = "sub done") -> StreamEvent:
    return StreamEvent(text=text, done=True)


def _parent(tmp_path, scripts=None, **kw) -> Agent:
    prov = ScriptProvider(scripts or [])
    return Agent(
        prov,
        new_default_registry(),
        "test",
        _engine(tmp_path),
        **kw,
    )


def test_name_and_parameters():
    t = AgentTool(_simple_catalog(), FakeTaskMgr())
    assert t.name() == "Agent"
    params = t.parameters()
    assert set(params["properties"]) == {
        "prompt",
        "description",
        "subagent_type",
        "model",
        "run_in_background",
        "name",
        "team_name",  # ch15：Team spawn 分支
    }
    assert params["required"] == ["prompt", "description"]
    assert "Explore" in t.description()


def test_missing_prompt(tmp_path):
    t = AgentTool(_simple_catalog(), FakeTaskMgr(), parent=_parent(tmp_path))

    async def go():
        return await t.execute(json.dumps({"description": "d"}))

    r = asyncio.run(go())
    assert r.is_error
    assert "prompt" in r.content


def test_missing_description(tmp_path):
    t = AgentTool(_simple_catalog(), FakeTaskMgr(), parent=_parent(tmp_path))

    async def go():
        return await t.execute(json.dumps({"prompt": "p"}))

    r = asyncio.run(go())
    assert r.is_error
    assert "description" in r.content


def test_not_wired(tmp_path):
    t = AgentTool(_simple_catalog(), FakeTaskMgr())  # parent=None

    async def go():
        return await t.execute(json.dumps({"prompt": "p", "description": "d"}))

    r = asyncio.run(go())
    assert r.is_error
    assert "not wired" in r.content


def test_unknown_subagent_type(tmp_path):
    t = AgentTool(_simple_catalog(), FakeTaskMgr(), parent=_parent(tmp_path))

    async def go():
        return await t.execute(
            json.dumps({"prompt": "p", "description": "d", "subagent_type": "nope"})
        )

    r = asyncio.run(go())
    assert r.is_error
    assert "unknown subagent_type: nope" in r.content


def test_known_type_runs_subagent(tmp_path):
    # 父 provider 是 mock 脚本；子 Agent 复用同一 provider 实例，返回纯文本
    prov = ScriptProvider([[_text("探索结果")]])
    parent = Agent(prov, new_default_registry(), "test", _engine(tmp_path))
    t = AgentTool(_simple_catalog(), FakeTaskMgr(), parent=parent)

    async def go():
        return await t.execute(
            json.dumps({"prompt": "去探索", "description": "探索", "subagent_type": "Explore"})
        )

    r = asyncio.run(go())
    assert not r.is_error
    assert r.content == "探索结果"
    # 子 Agent 的 system_prompt 用了 Explore 定义（覆盖）
    assert "你是探索 Agent。" in prov.requests[0].system.stable


def test_background_launch(tmp_path):
    parent = _parent(tmp_path)
    mgr = FakeTaskMgr()
    t = AgentTool(_simple_catalog(), mgr, parent=parent)

    async def go():
        return await t.execute(
            json.dumps(
                {
                    "prompt": "后台跑",
                    "description": "后台",
                    "subagent_type": "Explore",
                    "run_in_background": True,
                }
            )
        )

    r = asyncio.run(go())
    assert not r.is_error
    data = json.loads(r.content)
    assert data["status"] == "async_launched"
    assert data["task_id"] == "task_1"
    assert len(mgr.launched) == 1
    _, _sub_conv, name, task = mgr.launched[0]
    assert task == "后台跑"
    assert name == ""


def test_background_disabled(tmp_path):
    parent = _parent(tmp_path)
    t = AgentTool(_simple_catalog(), FakeTaskMgr(), parent=parent, bg_enabled=False)

    async def go():
        return await t.execute(
            json.dumps(
                {
                    "prompt": "p",
                    "description": "d",
                    "subagent_type": "Explore",
                    "run_in_background": True,
                }
            )
        )

    r = asyncio.run(go())
    assert r.is_error
    assert "background mode is disabled" in r.content


def test_fork_launches_background(tmp_path):
    parent = _parent(tmp_path)
    parent._active_conv = Conversation()
    parent._active_conv.add_user("前序消息")
    mgr = FakeTaskMgr()
    t = AgentTool(_simple_catalog(), mgr, parent=parent)

    async def go():
        return await t.execute(json.dumps({"prompt": "Fork 任务", "description": "fork"}))

    r = asyncio.run(go())
    assert not r.is_error
    data = json.loads(r.content)
    assert data["status"] == "async_launched"  # Fork 无条件后台
    assert len(mgr.launched) == 1
    sub_agent, sub_conv, _name, task = mgr.launched[0]
    assert task == "Fork 任务"
    # 子对话克隆了父对话 + Boilerplate（不含任务文本，任务由 launch 统一 add）
    texts = [m.content for m in sub_conv.messages()]
    assert "前序消息" in texts
    assert any("<fork_boilerplate>" in (t or "") for t in texts)
    # 子 Agent 工具集不含元工具（过滤生效）
    from novacode.tool.filter import ALL_AGENT_DISALLOWED_TOOLS

    allowed = set(sub_agent._allowed_tools or [])
    assert not (set(ALL_AGENT_DISALLOWED_TOOLS) & allowed)


def test_worktree_isolation_forces_foreground(tmp_path):
    """ch14 F23：isolation=worktree 时即使 run_in_background=True 也走前台，返回最终文本。"""
    from pathlib import Path

    from worktree_fixtures import init_repo

    from novacode.worktree import Manager

    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))
    prov = ScriptProvider([[_text("隔离结果")]])
    parent = Agent(prov, new_default_registry(), "test", _engine(tmp_path))
    mgr_fake = FakeTaskMgr()
    t = AgentTool(_isolation_catalog(), mgr_fake, parent=parent, worktree_mgr=mgr)

    async def go():
        return await t.execute(
            json.dumps(
                {
                    "prompt": "任务",
                    "description": "d",
                    "subagent_type": "wt",
                    "run_in_background": True,
                }
            )
        )

    r = asyncio.run(go())
    assert not r.is_error
    assert r.content == "隔离结果"  # 前台返回最终文本，非 task_id
    assert len(mgr_fake.launched) == 0  # 没走 task_mgr.launch
    # 隔离产生的临时 worktree 无变更 → auto_cleanup 已清理
    assert not list(Path(mgr.worktree_dir).iterdir())


def test_worktree_isolation_without_manager(tmp_path):
    """ch14 T12：worktree_mgr 未配置且 isolation=worktree 时返回错误。"""
    parent = _parent(tmp_path)
    t = AgentTool(_isolation_catalog(), FakeTaskMgr(), parent=parent)  # worktree_mgr=None

    async def go():
        return await t.execute(
            json.dumps({"prompt": "p", "description": "d", "subagent_type": "wt"})
        )

    r = asyncio.run(go())
    assert r.is_error
    assert "worktree manager not configured" in r.content


def test_timeout_adopts_background(tmp_path):
    import novacode.agent.agent_tool as at_mod

    old = at_mod.AUTO_BACKGROUND_SECONDS
    at_mod.AUTO_BACKGROUND_SECONDS = 0.05  # 调小触发超时
    try:
        parent = _parent(tmp_path)
        # 子 Agent 会一直跑（mock provider 挂起不结束）
        prov = ScriptProvider([])
        parent = Agent(prov, new_default_registry(), "test", _engine(tmp_path))
        mgr = FakeTaskMgr()
        t = AgentTool(_simple_catalog(), mgr, parent=parent)

        async def go():
            return await t.execute(
                json.dumps({"prompt": "慢任务", "description": "慢", "subagent_type": "Explore"})
            )

        r = asyncio.run(go())
        assert not r.is_error
        data = json.loads(r.content)
        assert data["status"] == "timed_out_to_background"
        assert len(mgr.adopted) == 1
    finally:
        at_mod.AUTO_BACKGROUND_SECONDS = old
