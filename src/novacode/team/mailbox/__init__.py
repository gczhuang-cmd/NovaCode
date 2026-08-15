"""邮箱 Box（ch15 T6，spec F33）。

每个收件人一个 `<mailbox>/<agent_id>.json`，写操作走同名 `.lock`（跨进程文件锁），
read-modify-write 后 `os.replace` 原子替换。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..filelock import acquire
from ..persistence import atomic_write_json
from .message import Message, MessageType

__all__ = ["Box", "Message", "MessageType"]


class Box:
    """一个 Team 的邮箱目录读写（跨进程文件锁）。"""

    def __init__(self, dir_: str) -> None:
        self._dir = dir_
        Path(dir_).mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> str:
        return str(Path(self._dir) / f"{agent_id}.json")

    def _lock_path(self, agent_id: str) -> str:
        return str(Path(self._dir) / f"{agent_id}.lock")

    async def write(self, agent_id: str, msg: Message) -> None:
        async with acquire(self._lock_path(agent_id)):
            data = self._load(agent_id)
            if msg.timestamp == 0:
                msg.timestamp = int(time.time())
            data["messages"].append(msg.to_dict())
            atomic_write_json(self._path(agent_id), data)

    async def read(self, agent_id: str) -> list[Message]:
        data = self._load(agent_id)
        return [Message.from_dict(m) for m in data["messages"]]

    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]:
        """返回 (unread indices, unread messages)。"""
        data = self._load(agent_id)
        indices: list[int] = []
        msgs: list[Message] = []
        for i, m in enumerate(data["messages"]):
            if not m.get("read"):
                indices.append(i)
                msgs.append(Message.from_dict(m))
        return indices, msgs

    async def mark_read(self, agent_id: str, indices: list[int]) -> None:
        if not indices:
            return
        async with acquire(self._lock_path(agent_id)):
            data = self._load(agent_id)
            for i in indices:
                if 0 <= i < len(data["messages"]):
                    data["messages"][i]["read"] = True
            atomic_write_json(self._path(agent_id), data)

    # ── 内部 ─────────────────────────────────────────────

    def _load(self, agent_id: str) -> dict:
        p = Path(self._path(agent_id))
        if not p.is_file():
            return {"messages": []}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"messages": []}
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            return {"messages": []}
        return data
