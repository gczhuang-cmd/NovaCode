"""filelock + mailbox 测试（ch15 T5/T6，spec G8/F33/AC14/AC15）。"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from novacode.team.filelock import acquire
from novacode.team.mailbox import Box, Message, MessageType


def test_acquire_serial(tmp_path):
    lock = str(tmp_path / "a.lock")

    async def go():
        async with acquire(lock):
            assert Path(lock).exists()
        assert not Path(lock).exists()

    asyncio.run(go())


def test_acquire_stale(tmp_path):
    """stale 锁（>10s）能被新 writer 抢占（AC15）。"""
    lock = tmp_path / "b.lock"
    lock.write_text("stale")
    os.utime(lock, (time.time() - 11, time.time() - 11))

    async def go():
        async with acquire(str(lock)):
            pass

    asyncio.run(go())
    assert not lock.exists()


def test_box_write_read_mark(tmp_path):
    async def go():
        box = Box(str(tmp_path / "mailbox"))
        await box.write(
            "alice",
            Message(from_="lead", to="alice", type=MessageType.TEXT, summary="hi", content="hello"),
        )
        indices, unread = await box.read_unread("alice")
        assert len(unread) == 1
        assert unread[0].summary == "hi"
        await box.mark_read("alice", indices)
        _, unread = await box.read_unread("alice")
        assert unread == []

    asyncio.run(go())


def test_box_concurrent_no_loss(tmp_path):
    """并发 10 task 写同一 mailbox，10 条无丢失（AC14）。"""

    async def go():
        box = Box(str(tmp_path / "mailbox"))

        async def _w(i: int) -> None:
            await box.write(
                "alice",
                Message(
                    from_="x", to="alice", type=MessageType.TEXT, summary=f"s{i}", content=str(i)
                ),
            )

        await asyncio.gather(*[_w(i) for i in range(10)])
        msgs = await box.read("alice")
        assert len(msgs) == 10
        assert {m.summary for m in msgs} == {f"s{i}" for i in range(10)}

    asyncio.run(go())


def test_acquire_timeout(tmp_path):
    """锁被持且非 stale 时，10 次重试后抛 TimeoutError（失败回归：不丢消息）。"""
    lock = tmp_path / "c.lock"
    lock.write_text("held")
    os.utime(lock, (time.time() - 1, time.time() - 1))  # 1 秒前，未超 10s stale

    async def go():
        try:
            async with acquire(str(lock)):
                pass
            assert False, "should raise TimeoutError"
        except TimeoutError:
            pass

    asyncio.run(go())


def test_box_broadcast_structure(tmp_path):
    async def go():
        box = Box(str(tmp_path / "mailbox"))
        await box.write(
            "lead",
            Message(
                from_="alice", to="lead", type=MessageType.TEXT, summary="idle", content="done"
            ),
        )
        data = (Path(tmp_path) / "mailbox" / "lead.json").read_text(encoding="utf-8")
        import json

        assert json.loads(data)["messages"][0]["from"] == "alice"

    asyncio.run(go())
