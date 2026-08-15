"""ActiveSkills：已激活 Skill 跨轮列表（ch11 T7）。"""

from __future__ import annotations

import threading

from .types import ActiveEntry


class ActiveSkills:
    """保持激活顺序的已激活 Skill 列表（F24）。

    重复激活同名 Skill 时更新 body、保持原位置（避免 env 块抖动）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ActiveEntry] = []
        self._index: dict[str, int] = {}

    def activate(self, name: str, body: str) -> None:
        """激活；已存在则更新 body（保持原位置），否则追加（F23）。"""
        with self._lock:
            if name in self._index:
                idx = self._index[name]
                self._entries[idx].body = body
            else:
                self._index[name] = len(self._entries)
                self._entries.append(ActiveEntry(name=name, body=body))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._index.clear()

    def snapshot(self) -> list[ActiveEntry]:
        with self._lock:
            return [ActiveEntry(e.name, e.body) for e in self._entries]

    def names(self) -> list[str]:
        with self._lock:
            return [e.name for e in self._entries]
