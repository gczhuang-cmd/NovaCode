"""4 个后台任务工具测试（ch13 T24）。"""

from __future__ import annotations

import asyncio
import json

from novacode.task.manager import Manager, Status
from novacode.task.tools import (
    SendMessageTool,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
)


class MockAgent:
    def __init__(self, results: list[str]) -> None:
        self.results = results
        self.calls = 0

    async def run_to_completion(self, conv, task="", events=None):
        self.calls += 1
        idx = min(self.calls - 1, len(self.results) - 1)
        text = self.results[idx]
        conv.add_assistant(text)
        return text


class _Conv:
    def __init__(self) -> None:
        self._msgs = []

    def add_assistant(self, text: str) -> None:
        self._msgs.append(type("M", (), {"role": "assistant", "content": text})())

    def add_user(self, text: str) -> None:
        self._msgs.append(type("M", (), {"role": "user", "content": text})())

    def messages(self):
        return list(self._msgs)


def _setup_manager():
    return Manager()


def test_task_list():
    async def go():
        m = _setup_manager()
        await m.launch(MockAgent(["a"]), _Conv(), "w1", "任务")
        await m.launch(MockAgent(["b"]), _Conv(), "", "任务2")
        r = await TaskListTool(m).execute("")
        assert not r.is_error
        data = json.loads(r.content)
        assert len(data) == 2
        assert {d["name"] for d in data} == {"w1", ""}
        assert all("status" in d and "tool_count" in d and "id" in d for d in data)

    asyncio.run(go())


def test_task_get_full_fields():
    async def go():
        m = _setup_manager()
        task_id = await m.launch(MockAgent(["x"]), _Conv(), "w2", "任务")
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        r = await TaskGetTool(m).execute(json.dumps({"task_id": task_id}))
        assert not r.is_error
        data = json.loads(r.content)
        assert data["id"] == task_id
        assert data["result"] == "x"
        assert data["status"] == "completed"
        assert "usage" in data and "start_time" in data

    asyncio.run(go())


def test_task_get_unknown_id():
    async def go():
        m = _setup_manager()
        r = await TaskGetTool(m).execute(json.dumps({"task_id": "nope"}))
        assert r.is_error
        assert "unknown task" in r.content

    asyncio.run(go())


def test_task_stop_cancels():
    async def go():
        m = _setup_manager()
        ag = MockAgent(["a"])
        task_id = await m.launch(ag, _Conv(), "w3", "任务")
        r = await TaskStopTool(m).execute(json.dumps({"task_id": task_id}))
        assert not r.is_error
        assert json.loads(r.content)["status"] == "cancellation_requested"
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        assert m.get(task_id).status is Status.CANCELLED

    asyncio.run(go())


def test_task_stop_unknown():
    async def go():
        m = _setup_manager()
        r = await TaskStopTool(m).execute(json.dumps({"task_id": "nope"}))
        assert r.is_error

    asyncio.run(go())


def test_send_message_resumes():
    async def go():
        m = _setup_manager()
        ag = MockAgent(["第一", "第二"])
        task_id = await m.launch(ag, _Conv(), "worker", "一")
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        r = await SendMessageTool(m).execute(json.dumps({"name": "worker", "message": "继续"}))
        assert not r.is_error
        data = json.loads(r.content)
        assert data["task_id"] == task_id
        assert data["status"] == "resumed"
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        assert m.get(task_id).result == "第二"

    asyncio.run(go())


def test_send_message_unknown_name():
    async def go():
        m = _setup_manager()
        r = await SendMessageTool(m).execute(json.dumps({"name": "ghost", "message": "hi"}))
        assert r.is_error

    asyncio.run(go())


def test_all_tools_is_system_false():
    m = _setup_manager()
    for t in (TaskListTool(m), TaskGetTool(m), TaskStopTool(m), SendMessageTool(m)):
        assert t.is_system is False
