"""TeamCreate 工具（ch15 T22，spec F20/F21）。"""

from __future__ import annotations

import json

from ...tool import Result


class TeamCreateTool:
    """创建 Team，主 Agent 升任 Lead。"""

    read_only = False
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamCreate"

    def description(self) -> str:
        return "创建一个 Agent 团队，把主 Agent 升任为 Lead。team_name 经清洗后作为团队目录名。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "团队名（经清洗后作为目录名）"},
                "description": {"type": "string", "description": "团队描述（可选）"},
                "agent_type": {"type": "string", "description": "保留位（可选，实际不使用）"},
            },
            "required": ["team_name"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="TeamCreate 参数应为 JSON 对象")
        team_name = str(data.get("team_name") or "").strip()
        if not team_name:
            return Result(is_error=True, content="team_name 必填")
        try:
            team = await self._mgr.create(
                team_name,
                str(data.get("description") or ""),
                str(data.get("agent_type") or ""),
            )
        except Exception as e:  # noqa: BLE001 结构化错误返回
            return Result(is_error=True, content=f"创建团队失败: {e}")
        return Result(
            content=json.dumps(
                {
                    "team_name": team.sanitized_name,
                    "backend": str(team.backend),
                    "config_path": team.config_path,
                },
                ensure_ascii=False,
            )
        )
