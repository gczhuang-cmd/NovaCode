"""TeamDelete 工具（ch15 T22，spec F22/F23）。"""

from __future__ import annotations

import json

from ...tool import Result


class TeamDeleteTool:
    """删除 Team；有活跃成员时需 force。"""

    read_only = False
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamDelete"

    def description(self) -> str:
        return "删除一个 Team（清 worktree、session、team 目录）。有活跃成员时需 force=True。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "团队名"},
                "force": {"type": "boolean", "description": "有活跃成员时也强制删除（默认 false）"},
            },
            "required": ["team_name"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="TeamDelete 参数应为 JSON 对象")
        team_name = str(data.get("team_name") or "").strip()
        if not team_name:
            return Result(is_error=True, content="team_name 必填")
        force = bool(data.get("force", False))
        try:
            await self._mgr.delete(team_name, force)
        except Exception as e:  # noqa: BLE001 结构化错误返回
            return Result(is_error=True, content=str(e))
        return Result(content=f"团队 {team_name} 已删除")
