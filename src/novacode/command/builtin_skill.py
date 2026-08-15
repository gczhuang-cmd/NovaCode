"""/skill 命令：列出已加载的 Skill（ch11 T22，Kind.LOCAL）。"""

from __future__ import annotations

from .context import CommandContext


async def handle_skill(ctx: CommandContext) -> None:
    """/skill：输出已加载 Skill 的精简列表（F34/F35）。"""
    if ctx.catalog is None:
        ctx.console.print("No skills loaded.")
        return
    skills = ctx.catalog.list()
    if not skills:
        ctx.console.print("No skills loaded.")
        return
    lines = [f"Available skills ({len(skills)}):"]
    for s in skills:  # Catalog.list 已按 name 字典序
        lines.append(f"  /{s.meta.name:<20} {s.meta.description}")
    lines.append("Type /<skill-name> to invoke a skill.")
    ctx.console.print("\n".join(lines))
