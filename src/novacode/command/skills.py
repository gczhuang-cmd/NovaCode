"""Skill → Slash Command 注册与移除（ch11 T23）。"""

from __future__ import annotations

import functools

from .command import Command, Kind
from .context import CommandContext
from .registry import Registry


async def _run_skill(ctx: CommandContext, name: str) -> None:
    """Skill 命令的 handler：经 ctx.executor 执行（本期零参数，F19）。

    executor 依赖 provider（run() 内选定后注入 ctx），故不在此闭包绑定。
    """
    if ctx.executor is None:
        ctx.console.print(f"[dim]skill executor 未就绪: {name}[/]")
        return
    await ctx.executor.execute(ctx, name, "")


def register_skills_as_commands(reg: Registry, catalog) -> None:
    """把 Catalog 中每个 Skill 注册为 Kind.PROMPT 命令（F18/F20）。

    - 命令名 = Skill 的 name
    - 描述 = description + " [skill]" 标记
    - is_skill = True（reload 时批量移除）
    用 functools.partial 显式绑定 name（Python 闭包后期绑定，循环里必须拷贝）。
    """
    for s in catalog.list():
        reg.register(
            Command(
                name=s.meta.name,
                description=s.meta.description + " [skill]",
                kind=Kind.PROMPT,
                handler=functools.partial(_run_skill, name=s.meta.name),
                is_skill=True,
            )
        )


def remove_skill_commands(reg: Registry) -> None:
    """移除全部 Skill 自动注册的命令（InstallSkill reload 用，F17）。"""
    reg.remove_if(lambda c: c.is_skill)
