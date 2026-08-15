"""Agent 新构造参数测试（ch13 T10）。"""

from __future__ import annotations

import asyncio

from novacode.agent import Agent
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission import Mode, Outcome, new_engine
from novacode.tool import new_default_registry


def _engine(tmp_path):
    engine, err = new_engine(str(tmp_path))
    assert err is None
    return engine


class ScriptProvider:
    """脚本化 provider：第 call_count 次请求返回 scripts[call_count-1]，超出重复最后一段。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.call_count = 0
        self.requests: list[Request] = []

    async def stream(self, req: Request):
        self.call_count += 1
        self.requests.append(req)
        idx = min(self.call_count - 1, len(self.scripts) - 1)
        for ev in self.scripts[idx]:
            yield ev


def _tool_script(name: str = "read_file") -> list[StreamEvent]:
    return [
        StreamEvent(tool_calls=[ToolCall(id="c1", name=name, input='{"path": "x"}')], done=True)
    ]


def _agent(tmp_path, **kw) -> Agent:
    return Agent(
        ScriptProvider([]),
        new_default_registry(),
        "test",
        _engine(tmp_path),
        **kw,
    )


def test_defaults(tmp_path):
    a = _agent(tmp_path)
    assert a._system_prompt is None
    assert a._max_turns == 0
    assert a._permission_mode is None
    assert a._dont_ask is False
    assert a._approval_upgrader is None


def test_accepts_all_options(tmp_path):
    async def up(_req):
        return Outcome.DENY_ONCE, True

    a = _agent(
        tmp_path,
        system_prompt="你是子 Agent",
        max_turns=7,
        permission_mode=Mode.ACCEPT_EDITS,
        dont_ask=True,
        approval_upgrader=up,
    )
    assert a._system_prompt == "你是子 Agent"
    assert a._max_turns == 7
    assert a._permission_mode is Mode.ACCEPT_EDITS
    assert a._dont_ask is True
    assert a._approval_upgrader is up


def test_system_prompt_override_reaches_request(tmp_path):
    prov = ScriptProvider([[StreamEvent(text="hi", done=True)]])
    a = Agent(
        prov,
        new_default_registry(),
        "test",
        _engine(tmp_path),
        system_prompt="覆盖后的系统提示",
    )

    async def go():
        conv = Conversation()
        async for _ev in a.run(conv, Mode.DEFAULT, asyncio.Event()):
            pass
        return prov.requests[0]

    req = asyncio.run(go())
    assert req.system.stable == "覆盖后的系统提示"


def test_max_turns_limits_loop(tmp_path):
    # 模型一直调工具（只读），max_turns=3 → 迭代 3 次后 stop
    prov = ScriptProvider([_tool_script()])
    a = Agent(prov, new_default_registry(), "test", _engine(tmp_path), max_turns=3)
    iters: list[int] = []

    async def go():
        conv = Conversation()
        async for ev in a.run(conv, Mode.DEFAULT, asyncio.Event()):
            if ev.iter:
                iters.append(ev.iter)
            if ev.done:
                break

    asyncio.run(go())
    assert max(iters) == 3
    assert len(iters) == 3


def test_max_turns_zero_uses_global(tmp_path):
    # max_turns=0 → 全局 MAX_ITERATIONS=25（用快速工具脚本 + 提前 break 避免跑满）
    prov = ScriptProvider([_tool_script()])
    a = Agent(prov, new_default_registry(), "test", _engine(tmp_path), max_turns=0)
    iters: list[int] = []

    async def go():
        conv = Conversation()
        async for ev in a.run(conv, Mode.DEFAULT, asyncio.Event()):
            if ev.iter:
                iters.append(ev.iter)
            if len(iters) >= 3:  # 不跑满 25，只验证上限未被收窄
                break

    asyncio.run(go())
    assert len(iters) >= 3
