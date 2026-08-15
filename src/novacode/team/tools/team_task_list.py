"""TeamTaskList 工具（ch15 T22，spec F28）。"""

from __future__ import annotations

import json

from ...tool import Result
from ..tasks import Filter, Store
from ._common import resolve_team


class TeamTaskListTool:
    """列出当前 Team 的共享任务，可按 status 过滤，带 is_ready 依赖标注。"""

    read_only = True
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamTaskList"

    def description(self) -> str:
        return "列出当前 Team 的共享任务；status 可选 pending/in_progress/completed/blocked，返回带 is_ready 字段。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "按状态过滤（可选）: pending / in_progress / completed / blocked",
                },
            },
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="TeamTaskList 参数应为 JSON 对象")
        status = str(data.get("status") or "").strip() or None
        try:
            team = resolve_team(self._mgr)
            tasks = await Store(team.tasks_path).list_(Filter(status=status))
        except Exception as e:  # noqa: BLE001
            return Result(is_error=True, content=str(e))
        return Result(content=json.dumps(tasks, ensure_ascii=False))
