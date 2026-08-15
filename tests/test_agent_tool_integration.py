"""Agent 工具端到端集成测试（ch13 T30）。"""

from __future__ import annotations

import asyncio
import json

from novacode.agent import Agent, Phase
from novacode.agent.agent_tool import AgentTool
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission import Mode, new_engine
from novacode.subagent.catalog import Catalog
from novacode.subagent.definition import Definition, Source
from novacode.task import Manager
from novacode.tool import new_default_registry


class ScriptProvider:
    """脚本化 provider（call_count 共享——子 Agent 复用父 provider 实例时按序取脚本）。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.call_count = 0

    async def stream(self, req: Request):
        self.call_count += 1
        idx = min(self.call_count - 1, len(self.scripts) - 1)
        for ev in self.scripts[idx]:
            yield ev


def _engine(tmp_path):
    engine, err = new_engine(str(tmp_path))
    assert err is None
    return engine


def _catalog() -> Catalog:
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


def _text(s: str) -> StreamEvent:
    return StreamEvent(text=s, done=True)


def _agent_tool_call(**kw) -> StreamEvent:
    return StreamEvent(
        tool_calls=[ToolCall(id="c1", name="Agent", input=json.dumps(kw))], done=True
    )


def test_main_agent_runs_foreground_subagent(tmp_path):
    """主 Agent 调 Agent 工具（Explore 前台）→ tool_result 是子 Agent final_text。"""
    prov = ScriptProvider(
        [
            [_agent_tool_call(prompt="去探索", description="探索", subagent_type="Explore")],
            [_text("子探索结果")],  # 子 Agent 第一轮（call 2）
            [_text("主 Agent 收尾")],  # 主 Agent 第二轮（call 3）
        ]
    )
    registry = new_default_registry()
    agent_tool = AgentTool(_catalog(), Manager(), parent=None)
    registry.register(agent_tool)
    parent = Agent(prov, registry, "test", _engine(tmp_path))
    agent_tool.set_parent(parent)

    tool_results: dict[str, str] = {}
    conv = Conversation()

    async def go():
        async for ev in parent.run(conv, Mode.BYPASS, asyncio.Event()):
            if ev.tool is not None and ev.tool.phase == Phase.END:
                tool_results[ev.tool.name] = ev.tool.result
            if ev.done:
                break

    asyncio.run(go())
    assert tool_results["Agent"] == "子探索结果"
    # 主 Agent 最终 assistant 文本（第二轮）
    assert conv.messages()[-1].content == "主 Agent 收尾"


def test_background_returns_async_launched(tmp_path):
    """run_in_background=True → 主 Agent 立即看到 async_launched JSON，不阻塞。"""
    prov = ScriptProvider(
        [
            [
                _agent_tool_call(
                    prompt="后台任务",
                    description="后台",
                    subagent_type="Explore",
                    run_in_background=True,
                )
            ],
            [_text("子结果")],  # 子 Agent 后台（call 2）
            [_text("主收尾")],  # 主 Agent 第二轮（call 3）
        ]
    )
    registry = new_default_registry()
    agent_tool = AgentTool(_catalog(), Manager(), parent=None)
    registry.register(agent_tool)
    parent = Agent(prov, registry, "test", _engine(tmp_path))
    agent_tool.set_parent(parent)

    tool_results: dict[str, str] = {}
    conv = Conversation()

    async def go():
        async for ev in parent.run(conv, Mode.BYPASS, asyncio.Event()):
            if ev.tool is not None and ev.tool.phase == Phase.END:
                tool_results[ev.tool.name] = ev.tool.result
            if ev.done:
                break

    asyncio.run(go())
    data = json.loads(tool_results["Agent"])
    assert data["status"] == "async_launched"
    assert data["task_id"].startswith("task_")


def test_catalog_and_filter_cooperate(tmp_path):
    """Catalog.resolve 拿 def → apply_agent_tool_filter 按 def 收窄（spec F30 协同）。"""
    from novacode.tool.filter import (
        ALL_AGENT_DISALLOWED_TOOLS,
        FilterParams,
        apply_agent_tool_filter,
    )

    defi = _catalog().resolve("Explore")
    assert defi is not None
    allowed = apply_agent_tool_filter(
        FilterParams(
            all=[
                "read_file",
                "grep",
                "glob",
                "write_file",
                "edit_file",
                "bash",
                "Agent",
                "TaskList",
                "TaskGet",
                "TaskStop",
                "SendMessage",
            ],
            source=int(defi.source),
            background=False,
            allowed=defi.tools,
            disallowed=defi.disallowed_tools,
        )
    )
    # 定义黑名单生效
    assert "write_file" not in allowed
    assert "edit_file" not in allowed
    assert "read_file" in allowed
    # 元工具被剔除
    assert not (set(ALL_AGENT_DISALLOWED_TOOLS) & set(allowed))
