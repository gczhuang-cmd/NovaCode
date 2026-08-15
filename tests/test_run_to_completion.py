"""run_to_completion 测试（ch13 T16）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from novacode.agent import Agent, Phase
from novacode.agent.permission_upgrade import default_upgrader
from novacode.agent.run_to_completion import MaxTurnsReached
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission import Outcome, new_engine
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
        idx = min(self.call_count - 1, len(self.scripts) - 1)
        for ev in self.scripts[idx]:
            yield ev


def _tool_call(name: str, input_: str, cid: str = "c1") -> StreamEvent:
    return StreamEvent(tool_calls=[ToolCall(id=cid, name=name, input=input_)], done=True)


def _text(text: str = "final answer") -> StreamEvent:
    return StreamEvent(text=text, done=True)


def _agent(tmp_path, scripts=None, **kw) -> Agent:
    return Agent(
        ScriptProvider(scripts or []),
        new_default_registry(),
        "test",
        _engine(tmp_path),
        **kw,
    )


def test_single_text_turn_returns_text(tmp_path):
    a = _agent(tmp_path, [[_text("hello")]])

    async def go():
        return await a.run_to_completion(Conversation(), "任务")

    assert asyncio.run(go()) == "hello"


def test_adds_user_when_task_nonempty(tmp_path):
    a = _agent(tmp_path, [[_text("ok")]])
    conv = Conversation()

    async def go():
        await a.run_to_completion(conv, "第一个任务")

    asyncio.run(go())
    assert any(m.role == "user" and m.content == "第一个任务" for m in conv.messages())


def test_task_empty_does_not_add_user(tmp_path):
    a = _agent(tmp_path, [[_text("ok")]])
    conv = Conversation()
    conv.add_user("预装填")

    async def go():
        await a.run_to_completion(conv, "")

    asyncio.run(go())
    # 没有追加空 user（run 自然完成会追加 assistant 回复在末尾）
    assert conv.messages()[-1].role == "assistant"
    assert not any(m.role == "user" and not m.content for m in conv.messages())


def test_tool_turn_then_text(tmp_path):
    a = _agent(
        tmp_path,
        [[_tool_call("read_file", '{"path": "x"}')], [_text("完成")]],
    )

    async def go():
        return await a.run_to_completion(Conversation(), "读文件")

    assert asyncio.run(go()) == "完成"


def test_max_turns_reached(tmp_path):
    # 一直调只读工具（mode=default 下 read_file Allow），max_turns=3 → 触达上限
    a = _agent(
        tmp_path,
        [[_tool_call("read_file", '{"path": "x"}')]],
        max_turns=3,
    )

    async def go():
        await a.run_to_completion(Conversation(), "跑", events=None)

    with pytest.raises(MaxTurnsReached):
        asyncio.run(go())


def test_dont_ask_allows_write(tmp_path):
    # write_file 在 default 下 Ask；dont_ask=True → 自动放行执行
    p = tmp_path / "out.txt"
    a = _agent(
        tmp_path,
        [
            [_tool_call("write_file", json.dumps({"path": str(p), "content": "hi"}))],
            [_text("写完了")],
        ],
        dont_ask=True,
    )

    async def go():
        return await a.run_to_completion(Conversation(), "写文件")

    assert asyncio.run(go()) == "写完了"
    assert p.read_text(encoding="utf-8") == "hi"


def test_default_upgrader_denies_write(tmp_path):
    # dont_ask=False + 默认 upgrader → write_file Ask 被拒，文件不创建
    p = tmp_path / "out2.txt"
    a = _agent(
        tmp_path,
        [
            [_tool_call("write_file", json.dumps({"path": str(p), "content": "x"}))],
            [_text("无法写入")],
        ],
        dont_ask=False,
        approval_upgrader=default_upgrader,
    )

    async def go():
        return await a.run_to_completion(Conversation(), "写文件")

    assert asyncio.run(go()) == "无法写入"
    assert not p.exists()


def test_upgrader_allow_once_executes(tmp_path):
    calls: list = []

    async def up(_req):
        calls.append(_req)
        return Outcome.ALLOW_ONCE, True

    p = tmp_path / "out3.txt"
    a = _agent(
        tmp_path,
        [
            [_tool_call("write_file", json.dumps({"path": str(p), "content": "y"}))],
            [_text("写完了")],
        ],
        approval_upgrader=up,
    )

    async def go():
        return await a.run_to_completion(Conversation(), "写文件")

    assert asyncio.run(go()) == "写完了"
    assert p.read_text(encoding="utf-8") == "y"
    assert len(calls) == 1
    assert calls[0].name == "write_file"


def test_events_queue_forwarding(tmp_path):
    a = _agent(
        tmp_path,
        [[_tool_call("read_file", '{"path": "x"}')], [_text("ok")]],
    )
    q: asyncio.Queue = asyncio.Queue(maxsize=16)
    collected: list = []

    async def go():
        await a.run_to_completion(Conversation(), "任务", events=q)
        while not q.empty():
            collected.append(q.get_nowait())

    asyncio.run(go())
    tools = [e for e in collected if getattr(e, "phase", None) is not None]
    assert any(t.name == "read_file" for t in tools)
    assert any(t.phase == Phase.END for t in tools)
