"""/worktree 命令：Worktree 手动管理（ch14 T13，spec F24-F29，Kind.UI）。

handler 经 `CommandContext` 访问能力（duck typing，command 包模块级不 import worktree 包）。
输出走 `ctx.console.print`（中文友好，N7）。
"""

from __future__ import annotations

from .context import CommandContext


async def handle_worktree(ctx: CommandContext, args: str) -> None:
    """解析 /worktree 子命令并执行。args 是尾部已 strip 的参数字符串（ch14 F29）。"""
    mgr = ctx.worktree_mgr
    if mgr is None:
        ctx.console.print("[bold red]Worktree 未启用（启动时管理器构造失败）[/]")
        return
    parts = args.split()
    if not parts:
        ctx.console.print(
            "[dim]用法: /worktree create <slug> | list | enter <slug> | "
            "exit [--remove] [--discard] | remove <slug> [--discard][/]"
        )
        return
    sub, rest = parts[0], parts[1:]
    try:
        if sub == "create":
            await _create(ctx, mgr, rest)
        elif sub == "list":
            await _list(ctx, mgr)
        elif sub == "enter":
            await _enter(ctx, mgr, rest)
        elif sub == "exit":
            await _exit(ctx, mgr, rest)
        elif sub == "remove":
            await _remove(ctx, mgr, rest)
        else:
            ctx.console.print(f"[bold red]未知子命令: {sub}[/]")
    except Exception as e:  # noqa: BLE001 命令异常兜底：可读错误（WorktreeHasChangesError 等）
        ctx.console.print(f"[bold red]{e}[/]")


async def _create(ctx: CommandContext, mgr, rest: list[str]) -> None:
    if len(rest) != 1:
        ctx.console.print("[dim]用法: /worktree create <slug>[/]")
        return
    wt = await mgr.create(rest[0], "HEAD", manual=True)  # 手动创建，不走自动清理
    ctx.console.print(f"[bold]Worktree 已创建: {wt.path} (分支 {wt.branch})[/]")


async def _list(ctx: CommandContext, mgr) -> None:
    sess_path = mgr.current_session.worktree_path if mgr.current_session is not None else None
    lines = []
    for wt in mgr.list():
        marks = []
        if wt.manual:
            marks.append("manual")
        if wt.path == sess_path:
            marks.append("active")
        suffix = f" [{'/'.join(marks)}]" if marks else ""
        lines.append(f"{wt.name}  {wt.path}  {wt.branch}{suffix}")
    ctx.console.print("\n".join(lines) if lines else "[dim]无 Worktree[/]")


async def _enter(ctx: CommandContext, mgr, rest: list[str]) -> None:
    if len(rest) != 1:
        ctx.console.print("[dim]用法: /worktree enter <slug>[/]")
        return
    session = await mgr.enter(rest[0])
    if ctx.set_active_cwd is not None:
        ctx.set_active_cwd(session.worktree_path)  # 主 Agent 下次 Run 按 worktree 路径注入 ctx
    ctx.console.print(f"[bold]已进入 {rest[0]}: {session.worktree_path}[/]")


async def _exit(ctx: CommandContext, mgr, rest: list[str]) -> None:
    # 函数内延迟 import：command 包模块级不依赖 worktree 包
    from ..worktree import ExitAction, ExitOptions

    remove = "--remove" in rest
    discard = "--discard" in rest
    sess = mgr.current_session
    if sess is None:
        ctx.console.print("[dim]当前没有活跃的 Worktree 会话[/]")
        return
    if remove:
        report = await mgr.exit(
            sess.worktree_name, ExitAction.REMOVE, ExitOptions(discard_changes=discard)
        )
        ctx.console.print(f"[bold]已退出并删除 Worktree: {report.path}[/]")
    else:
        await mgr.exit(sess.worktree_name, ExitAction.KEEP, ExitOptions())
        ctx.console.print(f"[dim]已退出 {sess.worktree_name}，回到主目录[/]")
    if ctx.set_active_cwd is not None:
        ctx.set_active_cwd("")  # 清空 active_cwd 回进程 cwd


async def _remove(ctx: CommandContext, mgr, rest: list[str]) -> None:
    from ..worktree import ExitOptions

    if not rest:
        ctx.console.print("[dim]用法: /worktree remove <slug> [--discard][/]")
        return
    slug = rest[0]
    discard = "--discard" in rest
    await mgr.remove(slug, ExitOptions(discard_changes=discard))
    ctx.console.print(f"[bold]已删除 Worktree: {slug}[/]")
