"""run_team_member 自治循环（ch15 T29，spec F19a/F19b）。

Pane 后端被 spawn 的 `python -m novacode --team-member` 子进程**不启动 TUI**，跑本模块：
读 mailbox → 分流消息 → run_to_completion → 通知 Lead idle → stdin Wake 等下一轮。
pane 内是只读日志流（print 事件），不接受用户输入（回车被 stdin reader 消费做 Wake）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from ..agent import Agent, Phase
from ..agent.permission_upgrade import default_upgrader
from ..agent.runtime import SessionRuntime
from ..agent.team_hook import TeammateContext, with_teammate_context
from ..agent.team_mailbox import ingest_team_mailbox
from ..compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from ..conversation import Conversation
from ..permission import Mode
from ..session import Writer, load_session
from ..tool.filter import TEAMMATE_TOOLS, FilterParams, apply_agent_tool_filter
from .mailbox import Box, Message, MessageType
from .spawn import build_team_context_reminder, team_system_prompt_suffix


async def run_team_member(
    team_mgr,
    provider,
    registry,
    engine,
    runtime,
    subagent_catalog,
    hook_engine,
    args,
) -> int:
    """Pane 后端子进程自治循环；返回退出码。"""
    os.chdir(args.worktree)
    team = team_mgr.get(args.team)
    if team is None:
        print(f"[team-member] team {args.team} not found", file=sys.stderr)
        return 1

    # 解析角色定义（agent_type 或 general-purpose）
    defi = None
    if args.agent_type and subagent_catalog is not None:
        defi = subagent_catalog.resolve(args.agent_type)
    if defi is None and subagent_catalog is not None:
        defi = subagent_catalog.resolve("general-purpose")
    if defi is None:
        print("[team-member] general-purpose 定义缺失", file=sys.stderr)
        return 1

    # 工具集装配（队员：filter 基础 + Team 协作工具；plan 模式只读基础 + 协作工具）
    base = apply_agent_tool_filter(
        FilterParams(
            all=[d.name for d in registry.definitions()],
            source=int(defi.source),
            background=False,
            allowed=defi.tools,
            disallowed=defi.disallowed_tools,
        )
    )
    full_allowed = base + list(TEAMMATE_TOOLS)
    if args.plan_mode:
        allowed = [n for n in base if registry.get(n) is not None and registry.get(n).read_only]
        allowed = allowed + list(TEAMMATE_TOOLS)
    else:
        allowed = full_allowed

    # 恢复会话（session_dir 已有历史则加载，否则空）——沿用 ch12 机制（F65）
    msgs, _ = load_session(args.session_dir)
    writer = Writer(args.session_dir)
    conv = (
        Conversation.from_messages(msgs, on_append=writer.on_append, on_replace=writer.on_replace)
        if msgs
        else Conversation(on_append=writer.on_append, on_replace=writer.on_replace)
    )

    # 构造队员 agent（强制 dont_ask，F39a）
    ses_ctx = new_session_context(args.worktree)
    sub_runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=ses_ctx,
        context_window=runtime.context_window,
    )
    sys_prompt = (defi.system_prompt or "") + team_system_prompt_suffix()
    sub_agent = Agent(
        provider,
        registry,
        _version(),
        engine,
        runtime=sub_runtime,
        allowed_tools=allowed,
        system_prompt=sys_prompt,
        max_turns=defi.max_turns,
        permission_mode=Mode.PLAN if args.plan_mode else defi.permission_mode,
        dont_ask=True,  # F39a：子进程无 TUI 接 ApprovalRequest
        approval_upgrader=default_upgrader,
        hook_engine=hook_engine,
    )
    box = Box(team.mailbox_dir)
    tc = TeammateContext(
        team_name=team.sanitized_name,
        member_name=args.member,
        agent_id=args.agent_id,
        backend_type=str(team.backend),
        mailbox_dir=team.mailbox_dir,
        read_unread=lambda: box.read_unread(args.agent_id),
        mark_read=lambda indices: box.mark_read(args.agent_id, indices),
    )
    # F40：<team-context> 追加进系统提示（agent_id 已知）
    sub_agent._system_prompt = (
        sys_prompt
        + "\n\n"
        + build_team_context_reminder(team, args.member, args.agent_id, args.worktree)
    )
    # F41/T32：每轮 mailbox 注入 + plan 审批切模式
    sub_agent._pre_iteration_reminders = ingest_team_mailbox(tc, sub_agent, full_allowed)

    # stdin reader：回车 → wake_event（tmux send-keys 唤醒路径）
    wake_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        loop.add_reader(sys.stdin.fileno(), wake_event.set)
    except (OSError, NotImplementedError):
        pass  # Windows 不支持 add_reader：靠 2s 兜底轮询（本后端仅在 WSL/Linux 使用）

    print(f"[team-member] {args.member} · team={team.sanitized_name} · cwd={args.worktree}")
    try:
        while True:
            if not Path(team.mailbox_dir).is_dir():
                print("[team-member] mailbox gone, exiting")
                break
            try:
                _, unread_msgs = await box.read_unread(args.agent_id)
            except (OSError, ValueError):
                unread_msgs = []
            if unread_msgs:
                await box.mark_read(args.agent_id, list(range(len(unread_msgs))))  # 近似：全部标读
                shutdown = False
                pending: list[str] = []
                for m in unread_msgs:
                    if m.type == MessageType.SHUTDOWN_REQUEST:
                        shutdown = True
                        await box.write(
                            team.lead_agent_id,
                            Message(
                                from_=args.member,
                                to=team.lead_agent_id,
                                type=MessageType.SHUTDOWN_RESPONSE,
                                summary="shutdown approved",
                                content="",
                                payload={"approve": True},
                            ),
                        )
                    elif m.type == MessageType.PLAN_APPROVAL_RESPONSE:
                        payload = m.payload or {}
                        if payload.get("approve"):
                            sub_agent.set_permission_mode(Mode.DEFAULT)
                            sub_agent.set_allowed_tools(full_allowed)
                            pending.append("Lead 已批准计划，请继续执行。")
                        else:
                            fb = payload.get("feedback", "")
                            pending.append(
                                "Lead 驳回了计划"
                                + (f"，反馈: {fb}" if fb else "")
                                + "。请调整后重新提交。"
                            )
                    else:
                        pending.append(m.content or m.summary)
                if shutdown:
                    print("[team-member] shutdown requested, exiting")
                    break
                for task_text in pending:
                    await run_teammate_task(sub_agent, conv, tc, task_text)
                # 通知 Lead idle（F45）
                try:
                    await team.set_member_active(args.member, False)
                    await box.write(
                        team.lead_agent_id,
                        Message(
                            from_=args.member,
                            to=team.lead_agent_id,
                            type=MessageType.TEXT,
                            summary=f"{args.member} idle",
                            content=f"agent {args.agent_id} finished work, available for new tasks",
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 通知失败不退出
                    print(f"[team-member] idle 通知失败: {exc}", file=sys.stderr)
            else:
                try:
                    await asyncio.wait_for(wake_event.wait(), timeout=2.0)
                except TimeoutError:
                    pass
                wake_event.clear()
    finally:
        try:
            loop.remove_reader(sys.stdin.fileno())
        except (OSError, NotImplementedError):
            pass
    return 0


async def run_teammate_task(sub_agent, conv, tc: TeammateContext, task_text: str) -> None:
    """跑一轮任务并打印事件流到 stdout（pane 内只读日志，F19b）。"""
    events: asyncio.Queue = asyncio.Queue(maxsize=64)
    collector = asyncio.create_task(_print_events(events))
    try:
        with with_teammate_context(tc):
            text = await sub_agent.run_to_completion(conv, task_text, events)
        print("─" * 60)
        print(text)
    except Exception as exc:  # noqa: BLE001 错误打 stderr 不退出
        print(f"[team-member] 任务出错: {exc}", file=sys.stderr)
    finally:
        await events.put(None)
        await asyncio.gather(collector, return_exceptions=True)


async def _print_events(events: asyncio.Queue) -> None:
    """事件流转 stdout：ToolEvent START 打 `● tool(args)`，其余静默。"""
    while True:
        item = await events.get()
        if item is None:
            break
        if getattr(item, "phase", None) == Phase.START:
            print(f"● {item.name}({item.args})")


def _version() -> str:
    from .. import __version__

    return __version__
