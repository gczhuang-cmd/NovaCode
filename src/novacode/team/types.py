"""Team 数据结构与异常（ch15 T1，spec F1/F2）。

- `Team` 封装一个小组的元数据与成员花名册；成员操作（add_member / set_member_active /
  remove_member）受 `Team._lock` 保护，加锁后先 `reload_from_disk_locked` 再改内存并
  原子保存（跨进程并发兜底，F19c）。
- `TeammateInfo` 一个队员的完整元信息，手写 `to_dict`/`from_dict`（细粒度控制
  `is_active` 的 None 语义）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .persistence import atomic_write_json, reload_from_disk_locked


class BackendType(StrEnum):
    """执行后端类型（spec F11）：tmux / iterm2 / in-process。"""

    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


class TeamError(Exception):
    """Team 模块错误统一基类（N7：中文消息）。"""


class TeamNotFoundError(TeamError):
    """按 sanitized name 找不到 Team。"""


class TeamHasActiveMembersError(TeamError):
    """Team 仍有活跃成员时删除被拒绝。"""


class MemberExistsError(TeamError):
    """Team 内成员名重复。"""


class MemberNotFoundError(TeamError):
    """Team 内找不到成员。"""


class InProcessTeammateNoSpawnError(TeamError):
    """in-process 队员不能嵌套 spawn Team 成员。"""


@dataclass
class TeammateInfo:
    """一个队员的完整元信息（spec F2）。"""

    name: str  # Lead 分配的队员名，Team 内唯一
    agent_id: str  # 实际使用的 agent id；in-process 为 task.BackgroundTask.id，Pane 为预生成
    agent_type: str = ""  # 使用的 subagent 定义名；Fork 路径下为 ""
    model: str = ""  # 模型覆盖，空表 inherit
    worktree_path: str = ""  # 绝对路径
    branch: str = ""  # 对应 worktree 分支名
    backend_type: BackendType = BackendType.IN_PROCESS  # 可 per-member 不同
    pane_id: str = ""  # tmux pane / iterm2 split id，in-process 为空
    is_active: bool | None = None  # None/True 活跃，False 空闲；终止后从 members 移除
    plan_mode_required: bool = False
    session_dir: str = ""  # 队员独立 session 目录绝对路径

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "model": self.model,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "backend_type": str(self.backend_type),
            "pane_id": self.pane_id,
            "is_active": self.is_active,
            "plan_mode_required": self.plan_mode_required,
            "session_dir": self.session_dir,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TeammateInfo:
        try:
            backend_type = BackendType(str(d.get("backend_type") or "in-process"))
        except ValueError:
            backend_type = BackendType.IN_PROCESS
        return cls(
            name=str(d.get("name") or ""),
            agent_id=str(d.get("agent_id") or ""),
            agent_type=str(d.get("agent_type") or ""),
            model=str(d.get("model") or ""),
            worktree_path=str(d.get("worktree_path") or ""),
            branch=str(d.get("branch") or ""),
            backend_type=backend_type,
            pane_id=str(d.get("pane_id") or ""),
            is_active=d.get("is_active"),
            plan_mode_required=bool(d.get("plan_mode_required", False)),
            session_dir=str(d.get("session_dir") or ""),
        )


@dataclass
class Team:
    """一个小组（spec F1）。`config_dir` 等派生路径不持久化，反序列化时填充。"""

    name: str  # 用户给的原始名
    sanitized_name: str  # 经 sanitize 后用于路径，Team 主键
    lead_agent_id: str  # 固定 "lead"（本期 Lead = 主 Agent）
    backend: BackendType  # 全 team 默认后端；可被 member 覆盖
    description: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now().astimezone()
    )  # tz-aware（DTZ005）
    members: list[TeammateInfo] = field(default_factory=list)

    # 派生路径（不持久化）
    config_dir: str = ""
    config_path: str = ""  # <config_dir>/config.json
    tasks_path: str = ""  # <config_dir>/tasks.json
    mailbox_dir: str = ""  # <config_dir>/mailbox/

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sanitized_name": self.sanitized_name,
            "lead_agent_id": self.lead_agent_id,
            "backend": str(self.backend),
            "description": self.description,
            "created_at": int(self.created_at.timestamp()),
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Team:
        try:
            backend = BackendType(str(d.get("backend") or "in-process"))
        except ValueError:
            backend = BackendType.IN_PROCESS
        ts = d.get("created_at") or 0
        return cls(
            name=str(d.get("name") or ""),
            sanitized_name=str(d.get("sanitized_name") or ""),
            lead_agent_id=str(d.get("lead_agent_id") or "lead"),
            backend=backend,
            description=str(d.get("description") or ""),
            created_at=datetime.fromtimestamp(int(ts), tz=UTC)
            if ts
            else datetime.now().astimezone(),
            members=[
                TeammateInfo.from_dict(m) for m in (d.get("members") or []) if isinstance(m, dict)
            ],
        )

    # ── 成员查询 ─────────────────────────────────────────

    def member_by_name(self, name: str) -> TeammateInfo | None:
        for m in self.members:
            if m.name == name:
                return m
        return None

    def member_by_agent_id(self, agent_id: str) -> TeammateInfo | None:
        for m in self.members:
            if m.agent_id == agent_id:
                return m
        return None

    # ── 成员操作（F8/F9/F10，加锁 + reload-before-modify） ──

    async def add_member(self, info: TeammateInfo) -> None:
        async with self._lock:
            await reload_from_disk_locked(self)
            if self.member_by_name(info.name) is not None:
                raise MemberExistsError(f"团队成员已存在: {info.name}")
            self.members.append(info)
            atomic_write_json(self.config_path, self.to_dict())

    async def set_member_active(self, name: str, active: bool) -> None:
        async with self._lock:
            await reload_from_disk_locked(self)
            m = self.member_by_name(name)
            if m is None:
                raise MemberNotFoundError(f"团队成员不存在: {name}")
            m.is_active = active
            atomic_write_json(self.config_path, self.to_dict())

    async def remove_member(self, name: str) -> None:
        async with self._lock:
            await reload_from_disk_locked(self)
            self.members = [m for m in self.members if m.name != name]
            atomic_write_json(self.config_path, self.to_dict())
