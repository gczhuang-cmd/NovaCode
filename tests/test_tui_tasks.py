"""TUI 后台任务通知注入测试（ch13 T27）。"""

from __future__ import annotations

import asyncio

from novacode.task.manager import BackgroundTask, Status
from novacode.tui.tasks import _inject_task_notifications, build_task_notification


def _bt(task_id: str, status=Status.COMPLETED, name: str = "", result: str = "") -> BackgroundTask:
    return BackgroundTask(
        id=task_id,
        name=name,
        sub_agent=object(),
        conv=object(),
        task="t",
        status=status,
        result=result,
    )


class _FakeMgr:
    def __init__(self) -> None:
        self.done: asyncio.Queue = asyncio.Queue()
        self.tasks: dict[str, BackgroundTask] = {}

    def subscribe_done(self) -> asyncio.Queue:
        return self.done

    def get(self, task_id: str):
        return self.tasks.get(task_id)


class _FakeRuntime:
    def __init__(self) -> None:
        self.notifs: list[str] = []

    def append_reminders(self, prompts: list[str]) -> None:
        self.notifs.extend(prompts)


def test_build_notification_completed():
    bt = _bt("task_1", Status.COMPLETED, name="worker", result="统计完成")
    text = build_task_notification(bt)
    assert text.startswith("<task-notification>")
    assert 'Task task_1 (name="worker"): completed' in text
    assert "Result: 统计完成" in text
    assert text.endswith("</task-notification>")


def test_build_notification_failed():
    bt = _bt("task_2", Status.FAILED, result="")
    bt.err = RuntimeError("boom")
    text = build_task_notification(bt)
    assert "failed" in text
    assert "boom" in text


def test_build_notification_cancelled():
    bt = _bt("task_3", Status.CANCELLED)
    text = build_task_notification(bt)
    assert "cancelled" in text


def test_inject_into_runtime():
    async def go():
        m = _FakeMgr()
        m.tasks["task_1"] = _bt("task_1", Status.COMPLETED, name="w", result="ok")
        m.done.put_nowait("task_1")
        runtime = _FakeRuntime()
        await _inject_task_notifications(m, runtime)
        assert len(runtime.notifs) == 1
        assert "<task-notification>" in runtime.notifs[0]
        assert "Result: ok" in runtime.notifs[0]

    asyncio.run(go())


def test_inject_multiple_in_order():
    async def go():
        m = _FakeMgr()
        m.tasks["t1"] = _bt("t1", result="a")
        m.tasks["t2"] = _bt("t2", result="b")
        m.done.put_nowait("t1")
        m.done.put_nowait("t2")
        runtime = _FakeRuntime()
        await _inject_task_notifications(m, runtime)
        assert len(runtime.notifs) == 2

    asyncio.run(go())


def test_inject_skips_unknown_and_empty():
    async def go():
        m = _FakeMgr()
        m.done.put_nowait("ghost")  # get 返回 None
        runtime = _FakeRuntime()
        await _inject_task_notifications(m, runtime)
        assert runtime.notifs == []

    asyncio.run(go())


def test_inject_drains_queue():
    async def go():
        m = _FakeMgr()
        m.tasks["t1"] = _bt("t1", result="r")
        m.done.put_nowait("t1")
        runtime = _FakeRuntime()
        await _inject_task_notifications(m, runtime)  # 第一次注入
        await _inject_task_notifications(m, runtime)  # 第二次：队列已空
        assert len(runtime.notifs) == 1

    asyncio.run(go())
