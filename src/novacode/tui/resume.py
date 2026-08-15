"""会话恢复：列表选择界面 + 恢复执行（ch09 F17~F23，prompt_toolkit 非全屏）。

`/resume` 在 run 主循环命令分发阶段触发（Agent 必不在运行，F46）。`do_resume`
先列出历史会话，经非全屏选择界面（仿 `_run_approval`）让用户 ↑↓ 选择 / 输入过滤 /
Enter 选中，再加载恢复并替换主循环的会话状态（holder）。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.text import Text

from .. import session
from ..compact import open_session_context
from ..compact.const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE
from ..compact.token import estimate_tokens
from ..conversation import Conversation
from ..llm import Message
from ..permission import Mode
from .view import assistant_markdown, tool_line, tool_result_summary, user_block

# 时间跨度提醒阈值（F21 第 5 步）
_TIME_GAP = timedelta(hours=6)


@dataclass
class SessionItem:
    """会话列表项的展示包装（F20）。"""

    info: session.SessionInfo

    @property
    def display_text(self) -> str:
        """`<title> · 3 hours ago · model · 1.3KB`。"""
        rel = _relative_time(self.info.modified_at)
        return f"{self.info.title} · {rel} · {self.info.model} · {_fmt_size(self.info.size)}"


def _relative_time(dt: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def _fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


def _fmt_duration(d: timedelta) -> str:
    total = int(d.total_seconds())
    if total < 3600:
        return f"{total // 60} 分钟"
    if total < 86400:
        return f"{total // 3600} 小时"
    return f"{total // 86400} 天"


def _render_history(console, msgs: list[Message]) -> None:
    """把恢复的消息历史逐条渲染进终端 scrollback（F23）。

    只写终端展示、不重新写入 JSONL（JSONL 是追加语义，历史行已存在）。
    内容为恢复的 msgs，含压缩后的摘要消息与时间跨度提醒。
    """
    console.print(Text(f"── 恢复会话历史（共 {len(msgs)} 条）──", style="dim"))
    for m in msgs:
        if m.role == "user":
            if m.content:
                console.print(user_block(m.content))
        elif m.role == "assistant":
            for tc in m.tool_calls:
                console.print(tool_line(tc.name, tc.input))
            if m.content:
                console.print(assistant_markdown(m.content))
        elif m.role == "tool":
            for tr in m.tool_results:
                console.print(tool_result_summary(tr.content, tr.is_error))
    console.print(Text("── 恢复会话历史结束 ──", style="dim"))


async def do_resume(ctx) -> None:
    """扫描历史会话 → 选择界面 → Enter 恢复 / Esc 取消（F18/F19）。"""
    items = session.list_sessions(ctx.sessions_dir)
    if not items:
        ctx.console.print("[dim]没有可恢复的历史会话。[/]")
        return
    selected = await _pick_session(items)
    if selected is None:
        ctx.console.print("[dim]已取消恢复。[/]")
        return
    await do_resume_session(ctx, selected)


async def _pick_session(items: list[session.SessionInfo]) -> session.SessionInfo | None:
    """非全屏选择界面：↑↓ 移动高亮、普通字符实时过滤、Enter 选择、Esc 取消。"""
    query = ""
    cursor = 0
    holder: dict = {}
    app_ref: dict = {}

    def _filtered() -> list[session.SessionInfo]:
        q = query.strip().lower()
        if not q:
            return items
        return [i for i in items if q in (i.title or "").lower()]

    def _render() -> FormattedText:
        fl = _filtered()
        if not fl:
            return FormattedText([("dim", "（无匹配会话）")])
        lines = []
        for idx, item in enumerate(fl):
            text = SessionItem(item).display_text
            # 每条目必须以 \n 结尾，prompt_toolkit 才会分行（ptk 不像 print 自动换行）
            if idx == cursor:
                lines.append(("bold reverse", f" ▸ {text}\n"))
            else:
                lines.append(("", f"   {text}\n"))
        return FormattedText(lines)

    def _clamp() -> None:
        nonlocal cursor
        fl = _filtered()
        if not fl:
            cursor = 0
            return
        cursor = cursor % len(fl)

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(_e) -> None:
        nonlocal cursor
        if _filtered():
            cursor = (cursor - 1) % len(_filtered())
        app_ref["app"].invalidate()

    @kb.add("down")
    @kb.add("j")
    def _down(_e) -> None:
        nonlocal cursor
        if _filtered():
            cursor = (cursor + 1) % len(_filtered())
        app_ref["app"].invalidate()

    @kb.add("enter")
    def _enter(_e) -> None:
        fl = _filtered()
        if fl:
            holder["selected"] = fl[cursor % len(fl)]
        app_ref["app"].exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(_e) -> None:
        app_ref["app"].exit()

    @kb.add("backspace")
    def _backspace(_e) -> None:
        nonlocal query, cursor
        if query:
            query = query[:-1]
            cursor = 0
            app_ref["app"].invalidate()

    @kb.add("<any>")
    def _type(_e) -> None:
        """普通可打印字符进过滤输入框（非全屏界面的输入即过滤，F19）。"""
        nonlocal query, cursor
        seq = _e.key_sequence
        ch = seq[0].text if seq else ""
        if len(ch) == 1 and ch.isprintable():
            query += ch
            cursor = 0
            app_ref["app"].invalidate()

    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(_render),
                    wrap_lines=True,
                    dont_extend_height=True,
                ),
                Window(
                    FormattedTextControl(
                        lambda: FormattedText(
                            [("", f"过滤: {query} · ↑↓ 选择 · Enter 确认 · Esc 取消")]
                        )
                    ),
                    height=1,
                    dont_extend_height=True,
                ),
            ]
        )
    )
    app = Application(layout=layout, full_screen=False, key_bindings=kb, mouse_support=False)
    app_ref["app"] = app
    await app.run_async()
    _clamp()
    return holder.get("selected")


async def do_resume_session(ctx, info: session.SessionInfo) -> None:
    """执行恢复：加载 → 超限压缩 → 时间提醒 → 重建会话状态（F21/F22）。"""
    msgs, last_ts = session.load_session(info.dir)
    if not msgs:
        ctx.console.print(f"[dim]会话 {info.id} 无可恢复消息，跳过。[/]")
        return
    # F21 第 4 步：token 超限自动压缩（复用 ch08 run_force_compact）
    est = estimate_tokens(0, msgs, 0)  # 恢复场景无 usage 锚点，全量估算
    limit = (
        (ctx.runtime.context_window if ctx.runtime is not None else 200000)
        - SUMMARY_RESERVE
        - AUTO_SAFETY_MARGIN
    )
    if est > limit:
        tmp_conv = Conversation.from_messages(msgs)
        defs = (
            ctx.registry.read_only_definitions()
            if ctx.mode_box.mode == Mode.PLAN
            else ctx.registry.definitions()
        )
        try:
            await ctx.agent.run_force_compact(tmp_conv, defs)
            msgs = tmp_conv.messages()
        except Exception as e:  # noqa: BLE001 压缩失败不阻断恢复
            ctx.console.print(f"[dim]恢复时压缩失败（继续恢复）: {e}[/]")
    # F21 第 5 步：时间跨度提醒（超 6 小时追加 user 消息）
    if last_ts is not None:
        dt = datetime.fromtimestamp(last_ts).astimezone()
        gap = datetime.now().astimezone() - dt
        if gap > _TIME_GAP:
            msgs = list(msgs) + [
                Message(
                    role="user",
                    content=(
                        f"[系统提示] 本会话已暂停 {_fmt_duration(gap)}。"
                        "部分上下文可能已过时，如需最新信息请重新读取相关文件。"
                    ),
                )
            ]
    # F22：重建 Writer（追加模式）/ Conversation / SessionContext，替换主循环状态
    new_writer = session.Writer.open_existing(info.dir)
    new_conv = Conversation.from_messages(msgs, new_writer.on_append, new_writer.on_replace)
    new_ses_ctx = open_session_context(ctx.root, info.id)
    if new_ses_ctx is None:
        ctx.console.print("[dim]会话目录不存在，无法恢复。[/]")
        new_writer.close()
        return
    # 记录启动会话（恢复前），用于清理 0 字节空壳
    old_writer = ctx.holder.writer
    old_ses_ctx = ctx.runtime.session if ctx.runtime is not None else None
    ctx.holder.conv = new_conv
    ctx.holder.writer = new_writer
    ctx.holder.ses_ctx = new_ses_ctx
    if ctx.runtime is not None:
        ctx.runtime.session = new_ses_ctx
    # 问题 3 方案 a：本次启动的会话若从未对话（0 字节空壳）且非恢复目标，删除它。
    # 覆盖「启动后直接 /resume」场景；「先对话再 /resume」的会话有内容不会被删。
    if (
        old_writer is not None
        and old_ses_ctx is not None
        and old_ses_ctx.session_id != new_ses_ctx.session_id
    ):
        try:
            old_writer.close()  # 先释放文件句柄，否则 Windows 上无法删除目录
        except Exception as e:  # noqa: BLE001 关闭失败不影响恢复
            logger.warning("关闭启动会话 Writer 失败: %s", e)
        old_jsonl = Path(old_ses_ctx.session_dir) / "conversation.jsonl"
        if old_jsonl.is_file() and old_jsonl.stat().st_size == 0:
            shutil.rmtree(old_ses_ctx.session_dir, ignore_errors=True)
    # F23：恢复完成后先把历史渲染进终端供回看，再显示系统消息
    _render_history(ctx.console, msgs)
    ctx.console.print(f"[bold]已恢复会话 {info.id}，共 {len(msgs)} 条消息[/]")
