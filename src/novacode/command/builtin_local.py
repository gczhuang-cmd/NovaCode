"""5 条纯本地命令：/help /status /memory /permission /session（ch10 T5）。

纯本地类只向用户输出信息，不修改对话历史、不改变界面运行模式、不消耗 token（F9）。
"""

from __future__ import annotations

from .command import Handler
from .context import CommandContext
from .registry import Registry

# /status 六个字段（key 含冒号，用于列对齐；顺序即 F19/N8 固定顺序）
_STATUS_KEYS = ("Mode:", "Tokens:", "Tools:", "Memories:", "Model:", "Directory:")


def make_help_handler(reg: Registry) -> Handler:
    """返回 /help handler：按字典序输出"命令名 + 描述"两列对齐（F18/AC1）。"""

    async def _handler(ctx: CommandContext) -> None:
        cmds = reg.visible()
        width = max(len(c.name) for c in cmds) if cmds else 0
        lines = [f"/{c.name.ljust(width)}  {c.description}" for c in cmds]
        ctx.console.print("\n".join(lines))

    return _handler


def _memory_files(ctx: CommandContext) -> list[str]:
    """合并项目层与用户层记忆文件名清单；mem_mgr 未接返回空（F20）。"""
    if ctx.mem_mgr is None:
        return []
    project, user = ctx.mem_mgr.list_files()
    return project + user


async def handle_status(ctx: CommandContext) -> None:
    """/status：6 行 key:value，按 F19 顺序两列对齐（N8）。"""
    usage_in = ctx.usage_box.usage_in if ctx.usage_box is not None else 0
    usage_out = ctx.usage_box.usage_out if ctx.usage_box is not None else 0
    model = ctx.provider.model if ctx.provider is not None else ""
    width = max(len(k) for k in _STATUS_KEYS)
    lines = [
        "novaCode Status",
        "",
        # str(Mode)：IntEnum 的 value 是整数，展示用 __str__（default/plan/acceptEdits/...）
        f"{'Mode:'.ljust(width)}  {ctx.mode_box.mode!s}",
        f"{'Tokens:'.ljust(width)}  {usage_in} in / {usage_out} out",
        f"{'Tools:'.ljust(width)}  {ctx.registry.count()} enabled",
        f"{'Memories:'.ljust(width)}  {len(_memory_files(ctx))} files",
        f"{'Model:'.ljust(width)}  {model}",
        f"{'Directory:'.ljust(width)}  {ctx.root}",
    ]
    ctx.console.print("\n".join(lines))


async def handle_memory(ctx: CommandContext) -> None:
    """/memory：列出已加载记忆文件名（只列文件名，不展开内容，F20）。"""
    files = _memory_files(ctx)
    if not files:
        ctx.console.print("无已加载的记忆文件")
        return
    ctx.console.print("\n".join(files))


async def handle_permission(ctx: CommandContext) -> None:
    """/permission：输出当前权限模式字符串（F21）。"""
    ctx.console.print(str(ctx.mode_box.mode))


async def handle_session(ctx: CommandContext) -> None:
    """/session：输出当前会话标识与存档路径（F22）。"""
    sid = ""
    if ctx.runtime is not None and ctx.runtime.session is not None:
        sid = ctx.runtime.session.session_id
    path = getattr(ctx.holder.writer, "path", "") if ctx.holder.writer is not None else ""
    ctx.console.print(f"Session: {sid}\nPath: {path}")
