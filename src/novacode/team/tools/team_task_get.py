"""TeamTaskGet 工具（ch15 T22，spec F27）。"""

from __future__ import annotations

import json

from ...tool import Result
from ..tasks import Store
from ._common import resolve_team


class TeamTaskGetTool:
    """按 task_id 取一条 Team 任务详情。"""

    read_only = True
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamTaskGet"

    def description(self) -> str:
        return "按 task_id 取当前 Team 的一条共享任务详情。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 id（task_ 开头）"},
            },
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="TeamTaskGet 参数应为 JSON 对象")
        task_id = str(data.get("task_id") or "").strip()
        if not task_id:
            return Result(is_error=True, content="task_id 必填")
        try:
            team = resolve_team(self._mgr)
            task = await Store(team.tasks_path).get(task_id)
        except Exception as e:  # noqa: BLE001
            return Result(is_error=True, content=str(e))
        if task is None:
            return Result(is_error=True, content=f"任务不存在: {task_id}")
        return Result(content=json.dumps(task.to_dict(), ensure_ascii=False))
