"""AgentNameRegistry：name ↔ agent_id 双向映射（ch15 T7，spec F35-F38）。

ch13 的 `task.Manager._by_name`（name → id 内部 dict）委托到本子包；后注册覆盖前
（弱引用，spec F38）。
"""

from __future__ import annotations

import threading


class AgentNameRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_name: dict[str, str] = {}  # name → agent_id
        self._by_id: dict[str, str] = {}  # agent_id → name（反查）

    def register(self, name: str, agent_id: str) -> None:
        with self._lock:
            old_id = self._by_name.get(name)
            if old_id is not None:
                self._by_id.pop(old_id, None)
            old_name = self._by_id.get(agent_id)
            if old_name is not None:
                self._by_name.pop(old_name, None)
            self._by_name[name] = agent_id
            self._by_id[agent_id] = name

    def unregister(self, name: str) -> None:
        with self._lock:
            agent_id = self._by_name.pop(name, None)
            if agent_id is not None:
                self._by_id.pop(agent_id, None)

    def unregister_by_agent_id(self, agent_id: str) -> None:
        with self._lock:
            name = self._by_id.pop(agent_id, None)
            if name is not None:
                self._by_name.pop(name, None)

    def resolve(self, name_or_id: str) -> str | None:
        """name 优先；否则若本身就是已知 agent_id 直接返回；否则 None。"""
        with self._lock:
            aid = self._by_name.get(name_or_id)
            if aid is not None:
                return aid
            if name_or_id in self._by_id:
                return name_or_id  # 直接是 agent_id
            return None

    def name_of(self, agent_id: str) -> str | None:
        with self._lock:
            return self._by_id.get(agent_id)

    def list_(self) -> dict[str, str]:
        with self._lock:
            return dict(self._by_name)
