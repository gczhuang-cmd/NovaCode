"""任务类型、过滤/补丁与 is_ready 计算（ch15 T8，spec F28/F30）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    """任务状态（spec F28）。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Task:
    """一条共享任务（spec F30）。"""

    id: str
    title: str
    description: str = ""
    status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": str(self.status),
            "assignee": self.assignee,
            "blocked_by": list(self.blocked_by),
            "blocks": list(self.blocks),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        try:
            status = Status(str(d.get("status") or "pending"))
        except ValueError:
            status = Status.PENDING
        return cls(
            id=str(d.get("id") or ""),
            title=str(d.get("title") or ""),
            description=str(d.get("description") or ""),
            status=status,
            assignee=str(d.get("assignee") or ""),
            blocked_by=[str(x) for x in (d.get("blocked_by") or [])],
            blocks=[str(x) for x in (d.get("blocks") or [])],
            created_at=int(d.get("created_at") or 0),
            updated_at=int(d.get("updated_at") or 0),
        )


@dataclass
class Filter:
    """list_ 的过滤条件；status 为空/None 表示不过滤。"""

    status: str | None = None


@dataclass
class Patch:
    """update 的增量字段；None/空列表 = 不修改。"""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    assignee: str | None = None
    add_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    remove_blocks: list[str] = field(default_factory=list)
    remove_blocked_by: list[str] = field(default_factory=list)


def is_ready(task: Task, all_tasks: dict[str, Task]) -> bool:
    """无未完成 blocker 才算 ready（spec F28）。"""
    for bid in task.blocked_by:
        bt = all_tasks.get(bid)
        if bt is None or bt.status != Status.COMPLETED:
            return False
    return True
