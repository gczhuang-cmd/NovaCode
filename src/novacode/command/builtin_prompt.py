"""提示词命令 /do（ch10 T7）。

ch11：内置 `/review` 已移除，改由 Skill 系统的 `/review`（mode=fork）接管
（review SKILL.md 见 novacode.skills.builtin）。
"""

from __future__ import annotations

from ..permission import Mode
from ..prompt import EXECUTE_DIRECTIVE
from .context import CommandContext


async def handle_do(ctx: CommandContext) -> None:
    """/do：切回 default 模式并按计划立即执行（F14，外部行为与实施前一致）。"""
    ctx.mode_box.mode = Mode.DEFAULT
    ctx.conv.add_user(EXECUTE_DIRECTIVE)
    await ctx.run_callback()
