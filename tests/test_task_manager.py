"""task.Manager 后台任务全生命周期测试（ch13 T22）。"""

from __future__ import annotations

import asyncio

import pytest

from novacode.task.manager import Manager, Status, TaskBusy, TaskNotFound


class MockAgent:
    """脚本化子 Agent：第 n 次 run_to_completion 返回第 n 段脚本。"""

    def __init__(self, results: list[str], fail_with: BaseException | None = None) -> None:
        self.results = results
        self.calls: list[tuple] = []  # (conv, task, events)
        self.fail_with = fail_with

    async def run_to_completion(self, conv, task="", events=None):
        self.calls.append((conv, task, events))
        if self.fail_with is not None:
            raise self.fail_with
        idx = min(len(self.calls) - 1, len(self.results) - 1)
        text = self.results[idx]
        conv.add_assistant(text)
        # 转发一个 tool 事件到 events 队列（测 tool_count）；sleep 让聚合器消费
        if events is not None:
            await events.put(type("Tool", (), {"phase": 0, "name": "bash"})())  # Phase.START=0
            await asyncio.sleep(0.05)
        return text


class _Conv:
    def __init__(self) -> None:
        self._msgs: list = []

    def add_assistant(self, text: str) -> None:
        self._msgs.append(type("M", (), {"role": "assistant", "content": text})())

    def add_user(self, text: str) -> None:
        self._msgs.append(type("M", (), {"role": "user", "content": text})())

    def messages(self):
        return list(self._msgs)


def _wait_done(m: Manager, task_id: str, timeout: float = 5.0) -> Status:
    async def go():
        q = m.subscribe_done()
        while True:
            got = await asyncio.wait_for(q.get(), timeout)
            if got == task_id:
                return m.get(task_id).status

    return asyncio.run(go())


def test_launch_completed():
    async def go():
        m = Manager()
        task_id = await m.launch(MockAgent(["结果A"]), _Conv(), "", "任务")
        bt = m.get(task_id)
        assert bt is not None
        assert bt.status is Status.RUNNING
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        assert bt.status is Status.COMPLETED
        assert bt.result == "结果A"
        assert bt.tool_count == 1  # mock 转发了一个 tool 事件
        assert bt.last_activity == "bash"

    asyncio.run(go())


def test_launch_failed():
    async def go():
        m = Manager()
        task_id = await m.launch(MockAgent([], fail_with=RuntimeError("boom")), _Conv(), "", "任务")
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        bt = m.get(task_id)
        assert bt.status is Status.FAILED
        assert isinstance(bt.err, RuntimeError)
        assert "boom" in str(bt.err)

    asyncio.run(go())


def test_stop_cancels():
    async def go():
        m = Manager()
        ag = MockAgent(["a"])
        task_id = await m.launch(ag, _Conv(), "", "任务")
        await m.stop(task_id)
        got = await asyncio.wait_for(m.subscribe_done().get(), 5)
        assert got == task_id
        assert m.get(task_id).status is Status.CANCELLED

    asyncio.run(go())


def test_stop_unknown_returns_false():
    async def go():
        m = Manager()
        assert await m.stop("nope") is False

    asyncio.run(go())


def test_send_message_resumes_same_id():
    async def go():
        m = Manager()
        ag = MockAgent(["第一轮", "第二轮"])
        task_id = await m.launch(ag, _Conv(), "worker1", "任务一")
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        assert m.get(task_id).status is Status.COMPLETED

        # 续派
        new_id = await m.send_message("worker1", "任务二")
        assert new_id == task_id  # 同 id 复用
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        bt = m.get(task_id)
        assert bt.status is Status.COMPLETED
        assert bt.result == "第二轮"
        # conv 追加了 user 消息
        assert any(m.role == "user" and m.content == "任务二" for m in bt.conv.messages())

    asyncio.run(go())


def test_send_message_unknown_name():
    async def go():
        m = Manager()
        with pytest.raises(TaskNotFound):
            await m.send_message("ghost", "hi")

    asyncio.run(go())


def test_send_message_busy():
    async def go():
        m = Manager()
        ag = MockAgent(["a"])
        task_id = await m.launch(ag, _Conv(), "busy", "任务")
        # 还在 running 时 send_message 应 raise TaskBusy
        with pytest.raises(TaskBusy):
            await m.send_message("busy", "hi")
        # 等它跑完
        await asyncio.wait_for(m.subscribe_done().get(), 5)
        assert m.get(task_id).status is Status.COMPLETED

    asyncio.run(go())


def test_by_name_overrides():
    async def go():
        m = Manager()
        t1 = await m.launch(MockAgent(["a"]), _Conv(), "dup", "一")
        t2 = await m.launch(MockAgent(["b"]), _Conv(), "dup", "二")
        assert t1 != t2
        assert m._by_name["dup"] == t2  # 后启动覆盖

    asyncio.run(go())


def test_list_order_by_start_time():
    async def go():
        m = Manager()
        t1 = await m.launch(MockAgent(["a"]), _Conv(), "", "一")
        t2 = await m.launch(MockAgent(["b"]), _Conv(), "", "二")
        ids = [t.id for t in m.list()]
        assert ids == [t1, t2]

    asyncio.run(go())


def test_adopt_running():
    async def go():
        m = Manager()
        ag = MockAgent(["结果"])
        conv = _Conv()
        # 模拟前台已启动的协程（继续跑）
        handle = asyncio.create_task(ag.run_to_completion(conv, "任务"))
        from novacode.task.manager import PartialState

        partial = PartialState()
        events: asyncio.Queue = asyncio.Queue()
        task_id = await m.adopt_running(ag, conv, "", events, handle, partial)
        got = await asyncio.wait_for(m.subscribe_done().get(), 5)
        assert got == task_id
        bt = m.get(task_id)
        assert bt.status is Status.COMPLETED
        assert bt.result == "结果"

    asyncio.run(go())


def test_upgrade_approval_default_deny():
    async def go():
        m = Manager()
        outcome, ok = await m.upgrade_approval(object())
        assert ok is True
        assert int(outcome) == 0  # Outcome.DENY_ONCE

    asyncio.run(go())
