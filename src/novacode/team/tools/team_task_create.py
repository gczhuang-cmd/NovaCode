"""TeamTaskCreate 工具（ch15 T22，spec F26）。"""

from __future__ import annotations

import json

from ...tool import Result
from ..tasks import Patch, Store, Task
from ._common import resolve_team


class TeamTaskCreateTool:
    """在 Team 共享任务列表创建一条任务。"""

    read_only = False
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamTaskCreate"

    def description(self) -> str:
        return "在当前 Team 的共享任务列表创建一条任务，返回 task_id。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题"},
                "description": {"type": "string", "description": "任务描述（可选）"},
                "assignee": {"type": "string", "description": "指派队员名（可选）"},
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "被哪些任务阻塞（可选，task_id 列表）",
                },
            },
            "required": ["title"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="TeamTaskCreate 参数应为 JSON 对象")
        title = str(data.get("title") or "").strip()
        if not title:
            return Result(is_error=True, content="title 必填")
        try:
            team = resolve_team(self._mgr)
            store = Store(team.tasks_path)
            task = Task(
                id="",
                title=title,
                description=str(data.get("description") or ""),
                assignee=str(data.get("assignee") or ""),
            )
            tid = await store.create(task)
            blocked_by = [str(x) for x in (data.get("blocked_by") or []) if x]
            if blocked_by:
                await store.update(tid, Patch(add_blocked_by=blocked_by))
        except Exception as e:  # noqa: BLE001 结构化错误返回
            return Result(is_error=True, content=str(e))
        return Result(content=json.dumps({"task_id": tid}))
