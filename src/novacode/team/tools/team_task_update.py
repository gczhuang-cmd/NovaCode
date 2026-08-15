"""TeamTaskUpdate 工具（ch15 T22，spec F29）。"""

from __future__ import annotations

import json

from ...tool import Result
from ..tasks import Patch, Store
from ._common import resolve_team


class TeamTaskUpdateTool:
    """更新一条 Team 任务；支持双向依赖维护（add_blocks / add_blocked_by）。"""

    read_only = False
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamTaskUpdate"

    def description(self) -> str:
        return (
            "更新一条 Team 任务：title/description/status/assignee 可改；"
            "add_blocks / add_blocked_by 双向维护依赖关系。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 id（task_ 开头）"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {
                    "type": "string",
                    "description": "pending / in_progress / completed / blocked",
                },
                "assignee": {"type": "string"},
                "add_blocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "本任务阻塞这些任务（可选）",
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "本任务被这些任务阻塞（可选）",
                },
                "remove_blocks": {"type": "array", "items": {"type": "string"}},
                "remove_blocked_by": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="TeamTaskUpdate 参数应为 JSON 对象")
        task_id = str(data.get("task_id") or "").strip()
        if not task_id:
            return Result(is_error=True, content="task_id 必填")

        def _str_list(key: str) -> list[str]:
            v = data.get(key)
            return [str(x) for x in v] if isinstance(v, list) else []

        patch = Patch(
            title=data.get("title"),
            description=data.get("description"),
            status=data.get("status"),
            assignee=data.get("assignee"),
            add_blocks=_str_list("add_blocks"),
            add_blocked_by=_str_list("add_blocked_by"),
            remove_blocks=_str_list("remove_blocks"),
            remove_blocked_by=_str_list("remove_blocked_by"),
        )
        try:
            team = resolve_team(self._mgr)
            task = await Store(team.tasks_path).update(task_id, patch)
        except Exception as e:  # noqa: BLE001
            return Result(is_error=True, content=str(e))
        if task is None:
            return Result(is_error=True, content=f"任务不存在: {task_id}")
        return Result(content=json.dumps(task.to_dict(), ensure_ascii=False))
