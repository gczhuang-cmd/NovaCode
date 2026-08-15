"""5 条影响界面命令：/exit /plan /compact /resume /clear（ch10 T6）。

影响界面类可以修改运行时模式、清空或替换会话、退出进程、触发会话恢复，
但不向对话历史追加 user 消息（F10）。
"""

from __future__ import annotations

import logging

from .. import session as session_pkg
from ..compact import new_session_context
from ..conversation import Conversation
from ..hook import Event as HookEvent
from ..permission import Mode
from .context import CommandContext, hook_base_payload

logger = logging.getLogger(__name__)


def _hook(ctx: CommandContext):
    """ctx 上的 Hook 引擎（未注入返回 None）。"""
    return getattr(ctx, "hook_engine", None)


async def _dispatch_session_end(ctx: CommandContext) -> None:
    h = _hook(ctx)
    if h is None:
        return
    await h.dispatch(
        HookEvent.SESSION_END,
        hook_base_payload(
            runtime=ctx.runtime, root=ctx.root, mode=ctx.mode_box.mode, event=HookEvent.SESSION_END
        ),
    )


async def _dispatch_session_start(ctx: CommandContext) -> None:
    h = _hook(ctx)
    if h is None:
        return
    result = await h.dispatch(
        HookEvent.SESSION_START,
        hook_base_payload(
            runtime=ctx.runtime,
            root=ctx.root,
            mode=ctx.mode_box.mode,
            event=HookEvent.SESSION_START,
        ),
    )
    if result.injected_prompts and ctx.runtime is not None:
        ctx.runtime.append_reminders(result.injected_prompts)


async def _dispatch_session_resume(ctx: CommandContext) -> None:
    h = _hook(ctx)
    if h is None:
        return
    result = await h.dispatch(
        HookEvent.SESSION_RESUME,
        hook_base_payload(
            runtime=ctx.runtime,
            root=ctx.root,
            mode=ctx.mode_box.mode,
            event=HookEvent.SESSION_RESUME,
        ),
    )
    if result.injected_prompts and ctx.runtime is not None:
        ctx.runtime.append_reminders(result.injected_prompts)


async def _reset_hook(ctx: CommandContext) -> None:
    """清空 Hook 引擎 only_once 集合（/clear、/resume 进新会话，F27/N5）。"""
    h = _hook(ctx)
    if h is not None:
        await h.reset_for_new_session()


async def handle_exit(ctx: CommandContext) -> None:
    """/exit：置退出标志，run 主循环据此 break（F12）。

    ch12：SessionEnd 由 cli 退出兜底统一 emit（`/exit` 与 Ctrl+C 都走 run 返回，
    此处不 dispatch，避免与兜底重复触发）。
    """
    ctx.exit_flag[0] = True


async def handle_plan(ctx: CommandContext) -> None:
    """/plan：切到计划模式（仅只读工具），F13。"""
    ctx.mode_box.mode = Mode.PLAN
    ctx.console.print("[dim]已进入计划模式（仅只读工具）。输入 /do 开始执行。[/]")


async def handle_compact(ctx: CommandContext) -> None:
    """/compact：手动触发上下文压缩，打印 token 前后变化（F15）。

    沿用 ch09 既有逻辑；idle 守护由主循环空闲阶段天然满足，无需检查（N3a）。
    """
    defs = (
        ctx.registry.read_only_definitions()
        if ctx.mode_box.mode == Mode.PLAN
        else ctx.registry.definitions()
    )
    try:
        before, after = await ctx.agent.run_force_compact(ctx.conv, defs)
    except Exception as e:  # noqa: BLE001 压缩失败提示，不退出
        ctx.console.print(f"[bold red]压缩失败: {e}[/]")
        return
    ctx.console.print(f"[dim]已压缩，token 从 {before} 降至 {after}[/]")


async def handle_resume(ctx: CommandContext) -> None:
    """/resume：打开历史会话列表并恢复（ch09 F17~F23，行为不变）。

    命令分发阶段 Agent 必不在运行，天然互斥（F46）；无会话或取消时不改变当前会话。
    ch12：切换离开旧会话前 emit SessionEnd；恢复成功后 emit SessionResume + 重置 only_once。
    """
    from ..tui.resume import do_resume

    old_sid = ""
    if ctx.runtime is not None and ctx.runtime.session is not None:
        old_sid = ctx.runtime.session.session_id
    await _dispatch_session_end(ctx)
    await do_resume(ctx)
    new_sid = ""
    if ctx.runtime is not None and ctx.runtime.session is not None:
        new_sid = ctx.runtime.session.session_id
    if new_sid and new_sid != old_sid:
        # 确实切换到新会话：清 only_once 并 emit SessionResume（首条 user 消息进入之前）
        await _reset_hook(ctx)
        await _dispatch_session_resume(ctx)


async def handle_clear(ctx: CommandContext) -> None:
    """/clear：结束当前会话，开启新会话（F17/N9）。

    关旧 writer → 建新 session/writer → 重建 Conversation（挂新回调）→
    替换 holder 与 runtime → 归零 usage。终端 scrollback 不编程清除
    （spec 修订：scrollback 属终端历史）；旧会话目录保留，/resume 仍可恢复。
    """
    old_id = ""
    if ctx.runtime is not None and ctx.runtime.session is not None:
        old_id = ctx.runtime.session.session_id
    # ch12：SessionEnd hook（关闭旧会话前）
    await _dispatch_session_end(ctx)
    old_writer = ctx.holder.writer
    if old_writer is not None:
        try:
            old_writer.close()
        except Exception as e:  # noqa: BLE001 关闭失败不影响开新会话
            logger.warning("关闭旧会话 Writer 失败: %s", e)
    try:
        new_ses_ctx = new_session_context(ctx.root)
    except Exception as e:  # noqa: BLE001 新会话创建失败，提示后中止
        ctx.console.print(f"[bold red]开启新会话失败: {e}[/]")
        return
    new_writer = session_pkg.Writer(new_ses_ctx.session_dir)
    if ctx.provider is not None:
        new_writer.set_model(ctx.provider.model)
    # 重建 Conversation 挂上新 writer 回调（旧 writer 已关闭，其 hook 已失效）
    new_conv = Conversation(
        on_append=new_writer.on_append,
        on_replace=new_writer.on_replace,
    )
    ctx.holder.conv = new_conv
    ctx.holder.writer = new_writer
    ctx.holder.ses_ctx = new_ses_ctx
    if ctx.runtime is not None:
        ctx.runtime.reset_for_new_session(new_ses_ctx)
    if ctx.usage_box is not None:
        ctx.usage_box.usage_in = 0
        ctx.usage_box.usage_out = 0
    # ch11 F25/N9：新会话从干净激活态开始——清空已激活 Skill（env context 剔除旧 SOP）
    if ctx.runtime is not None and getattr(ctx.runtime, "active_skills", None) is not None:
        ctx.runtime.active_skills.clear()
    # ch12：only_once 重置 + SessionStart hook（新会话，env context 装配完毕后）
    await _reset_hook(ctx)
    await _dispatch_session_start(ctx)
    ctx.console.print(
        f"[bold]已结束当前会话（{old_id}），开启新会话（{new_ses_ctx.session_id}）。"
        "旧会话可用 /resume 恢复。[/]"
    )
