"""tasks Store 测试（ch15 T8，spec F26-F30）。"""

from __future__ import annotations

import asyncio

from novacode.team.tasks import Filter, Patch, Status, Store, Task


def _run(coro):
    return asyncio.run(coro)


def test_create_id_format(tmp_path):
    async def go():
        store = Store(str(tmp_path / "tasks.json"))
        tid = await store.create(Task(id="", title="t1"))
        assert tid.startswith("task_") and len(tid) == len("task_") + 6

    asyncio.run(go())


def test_crud(tmp_path):
    async def go():
        store = Store(str(tmp_path / "tasks.json"))
        tid = await store.create(Task(id="", title="t1", assignee="alice"))
        t = await store.get(tid)
        assert t.title == "t1" and t.assignee == "alice"
        await store.update(tid, Patch(status="completed"))
        assert (await store.get(tid)).status == Status.COMPLETED

    asyncio.run(go())


def test_dependency_bidirectional(tmp_path):
    async def go():
        store = Store(str(tmp_path / "tasks.json"))
        a = await store.create(Task(id="", title="a"))
        b = await store.create(Task(id="", title="b"))
        await store.update(b, Patch(add_blocked_by=[a]))
        tb = await store.get(b)
        ta = await store.get(a)
        assert a in tb.blocked_by  # b 被 a 阻塞
        assert b in ta.blocks  # a 阻塞 b（双向维护）

    asyncio.run(go())


def test_is_ready(tmp_path):
    async def go():
        store = Store(str(tmp_path / "tasks.json"))
        a = await store.create(Task(id="", title="a"))
        b = await store.create(Task(id="", title="b", blocked_by=[a]))
        bd = next(x for x in await store.list_(Filter(status="pending")) if x["id"] == b)
        assert bd["is_ready"] is False  # blocker a 未完成
        await store.update(a, Patch(status="completed"))
        bd = next(x for x in await store.list_() if x["id"] == b)
        assert bd["is_ready"] is True  # blocker 全部 completed

    asyncio.run(go())


def test_status_filter(tmp_path):
    async def go():
        store = Store(str(tmp_path / "tasks.json"))
        await store.create(Task(id="", title="p", status=Status.PENDING))
        await store.create(Task(id="", title="c", status=Status.COMPLETED))
        only_pending = await store.list_(Filter(status="pending"))
        assert [t["title"] for t in only_pending] == ["p"]

    asyncio.run(go())
