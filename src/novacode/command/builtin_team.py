"""/team 命令：团队管理（ch15 T27，spec F59-F62，Kind.LOCAL + args_handler）。

handler 经 `CommandContext` 访问能力（duck typing，command 包模块级不 import team 包）。
输出走 `ctx.console.print`（中文友好，N7）。
"""

from __future__ import annotations

from .context import CommandContext


async def handle_team(ctx: CommandContext, args: str) -> None:
    """解析 /team 子命令并执行。args 是尾部已 strip 的参数字符串（ch14 F29 模式）。"""
    mgr = ctx.team_mgr
    if mgr is None:
        ctx.console.print("[bold red]Team 功能未启用（team_mgr 未注入）[/]")
        return
    parts = args.split()
    if not parts:
        ctx.console.print(
            "[dim]用法: /team list | info <name> | delete <name> [--force] | kill <member>[/]"
        )
        return
    sub, rest = parts[0], parts[1:]
    try:
        if sub == "list":
            await _list(ctx, mgr)
        elif sub == "info":
            await _info(ctx, mgr, rest)
        elif sub == "delete":
            await _delete(ctx, mgr, rest)
        elif sub == "kill":
            await _kill(ctx, mgr, rest)
        else:
            ctx.console.print(f"[bold red]未知子命令: {sub}[/]")
    except Exception as e:  # noqa: BLE001 命令异常兜底：可读错误
        ctx.console.print(f"[bold red]{e}[/]")


async def _list(ctx: CommandContext, mgr) -> None:
    teams = mgr.list_()
    if not teams:
        ctx.console.print("[dim]无团队[/]")
        return
    for t in teams:
        active = sum(1 for m in t.members if m.is_active is not False)
        total = len(t.members)
        ctx.console.print(f"{t.sanitized_name}  {t.backend}  {total} 成员  [{active}/{total}] 活跃")


async def _info(ctx: CommandContext, mgr, rest: list[str]) -> None:
    if not rest:
        ctx.console.print("[dim]用法: /team info <name>[/]")
        return
    t = mgr.get(rest[0])
    if t is None:
        ctx.console.print(f"[bold red]未找到团队: {rest[0]}[/]")
        return
    lines = [
        f"团队: {t.name} ({t.sanitized_name})",
        f"后端: {t.backend}  创建: {t.created_at:%Y-%m-%d %H:%M}",
        f"配置: {t.config_path}",
    ]
    for m in t.members:
        lines.append(
            f"  {m.name}  agent={m.agent_id}  backend={m.backend_type}  "
            f"active={m.is_active}  worktree={m.worktree_path}"
        )
    ctx.console.print("\n".join(lines))


async def _delete(ctx: CommandContext, mgr, rest: list[str]) -> None:
    if not rest:
        ctx.console.print("[dim]用法: /team delete <name> [--force][/]")
        return
    name, force = rest[0], "--force" in rest
    await mgr.delete(name, force)
    ctx.console.print(f"[bold]团队 {name} 已删除[/]")


async def _kill(ctx: CommandContext, mgr, rest: list[str]) -> None:
    if not rest:
        ctx.console.print("[dim]用法: /team kill <member>[/]")
        return
    member = rest[0]
    from ..team.backend import new_backend  # 函数内延迟 import（command 包不依赖 team）

    for t in mgr.list_():
        m = t.member_by_name(member)
        if m is None:
            continue
        backend = new_backend(m.backend_type, task_mgr=mgr.task_mgr)
        await backend.kill(m.pane_id, m.agent_id)
        await t.remove_member(member)
        ctx.console.print(f"[bold]已终止并移除成员 {member}[/]")
        return
    ctx.console.print(f"[bold red]未找到成员: {member}[/]")
