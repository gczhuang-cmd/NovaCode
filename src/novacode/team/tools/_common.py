"""Team 工具公共定位（ch15 T22）。

独立子模块避免 `tools/__init__.py` 与工具文件的循环导入。
"""

from __future__ import annotations

from ..types import Team, TeamNotFoundError


def resolve_team(mgr) -> Team:
    """按当前 ctx 定位 Team：队员经 TeammateContext，Lead 走 active team。"""
    from ...agent.team_hook import teammate_context_from_ctx

    tc = teammate_context_from_ctx(None)
    if tc is not None and tc.team_name:
        team = mgr.get(tc.team_name)
        if team is None:
            raise TeamNotFoundError(f"未找到团队: {tc.team_name}")
        return team
    team = mgr.get_active_team()
    if team is None:
        raise RuntimeError("当前没有活跃 Team（先调 TeamCreate，或作为队员运行）")
    return team
