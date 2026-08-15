"""/hooks 命令：列出已加载 hook 列表 + 加载来源（ch12 T21，Kind.LOCAL）。"""

from __future__ import annotations

from .context import CommandContext


async def handle_hooks(ctx: CommandContext) -> None:
    """/hooks：输出已加载 hook 的精简列表（F34/F35）。

    按 event 分组（保留 yaml 声明顺序）、每条一行 `  <name>  <event>  <action.type>  <flags>`；
    末尾追加 `Loaded from: <来源文件列表>`。无 hook 时输出 `No hooks loaded.`。
    """
    engine = getattr(ctx, "hook_engine", None)
    if engine is None or not engine.rules:
        ctx.console.print("No hooks loaded.")
        return
    groups: dict[str, list] = {}
    for r in engine.rules:
        groups.setdefault(r.event.value, []).append(r)
    lines: list[str] = []
    for event_name, rules in groups.items():
        lines.append(f"{event_name}:")
        for r in rules:
            flags = []
            if r.only_once:
                flags.append(r"\[once]")  # rich 会把 [..] 当样式标签，需转义
            if r.asyncio_mode:
                flags.append(r"\[async]")
            flag_str = (" " + " ".join(flags)) if flags else ""
            lines.append(f"  {r.name}  {r.event.value}  {r.action.type.value}{flag_str}")
    sources = engine.sources
    if sources:
        lines.append(f"Loaded from: {', '.join(sources)}")
    ctx.console.print("\n".join(lines))
