"""共享任务列表 Store（ch15 T8，spec F26-F30）。

`<team_config_dir>/tasks.json` 单文件存储；read-modify-write 走 `team.filelock` 文件锁
（tasks.json.lock）。`is_ready` 在 list_ 输出时计算，不存盘。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path

from ..filelock import acquire
from ..persistence import atomic_write_json
from .filter import Filter, Patch, Status, Task, is_ready

__all__ = ["Filter", "Patch", "Status", "Store", "Task", "is_ready"]


class Store:
    def __init__(self, path: str) -> None:
        self._path = path  # <team_config_dir>/tasks.json
        self._lock = asyncio.Lock()  # 进程内串行；跨进程靠文件锁

    def _lock_path(self) -> str:
        return self._path + ".lock"

    # ── 内部读写（调用方持锁） ──

    def _load(self) -> dict[str, Task]:
        p = Path(self._path)
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        tasks = data.get("tasks") or []
        out: dict[str, Task] = {}
        for t in tasks:
            if isinstance(t, dict) and t.get("id"):
                out[str(t["id"])] = Task.from_dict(t)
        return out

    def _save(self, tasks: dict[str, Task]) -> None:
        atomic_write_json(self._path, {"tasks": [t.to_dict() for t in tasks.values()]})

    # ── 公开接口 ──

    async def create(self, t: Task) -> str:
        async with self._lock, acquire(self._lock_path()):
            tasks = self._load()
            tid = f"task_{secrets.token_hex(3)}"
            while tid in tasks:
                tid = f"task_{secrets.token_hex(3)}"
            t.id = tid
            now = int(time.time())
            t.created_at = t.created_at or now
            t.updated_at = now
            tasks[tid] = t
            self._save(tasks)
            return tid

    async def get(self, id_: str) -> Task | None:
        return self._load().get(id_)

    async def list_(self, f: Filter | None = None) -> list[dict]:
        """按 status 过滤，返回带 is_ready 字段的 dict 列表（spec F28）。"""
        async with self._lock:
            tasks = self._load()
        f = f or Filter()
        out: list[dict] = []
        for t in tasks.values():
            if f.status and str(t.status) != f.status:
                continue
            d = t.to_dict()
            d["is_ready"] = is_ready(t, tasks)
            out.append(d)
        out.sort(key=lambda x: x["created_at"])
        return out

    async def update(self, id_: str, p: Patch) -> Task | None:
        """更新任务并双向维护依赖关系；返回更新后的任务（不存在返回 None）。"""
        async with self._lock, acquire(self._lock_path()):
            tasks = self._load()
            t = tasks.get(id_)
            if t is None:
                return None
            changed = False
            if p.title is not None:
                t.title, changed = p.title, True
            if p.description is not None:
                t.description, changed = p.description, True
            if p.status is not None:
                t.status, changed = Status(p.status), True
            if p.assignee is not None:
                t.assignee, changed = p.assignee, True
            # add_blocks：本任务 blocks += x，同时 x.blocked_by += 本任务
            for x in p.add_blocks:
                if x not in t.blocks:
                    t.blocks.append(x)
                    changed = True
                other = tasks.get(x)
                if other is not None and id_ not in other.blocked_by:
                    other.blocked_by.append(id_)
            # add_blocked_by：本任务 blocked_by += x，同时 x.blocks += 本任务
            for x in p.add_blocked_by:
                if x not in t.blocked_by:
                    t.blocked_by.append(x)
                    changed = True
                other = tasks.get(x)
                if other is not None and id_ not in other.blocks:
                    other.blocks.append(id_)
            for x in p.remove_blocks:
                if x in t.blocks:
                    t.blocks.remove(x)
                    changed = True
                other = tasks.get(x)
                if other is not None and id_ in other.blocked_by:
                    other.blocked_by.remove(id_)
            for x in p.remove_blocked_by:
                if x in t.blocked_by:
                    t.blocked_by.remove(x)
                    changed = True
                other = tasks.get(x)
                if other is not None and id_ in other.blocks:
                    other.blocks.remove(id_)
            if changed:
                t.updated_at = int(time.time())
                self._save(tasks)
            return t
