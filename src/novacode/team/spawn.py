"""spawn_teammate 主流程（ch15 T18，spec F25）。

`Manager.spawn_teammate` 委托本模块函数。职责：校验 → 解析定义 → 创建 Worktree →
申请 session → 构造 in-process 子 Agent（或 Pane SpawnRequest）→ backend.spawn →
注册 AgentNameRegistry → 写入 team.members。
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

from ..agent.team_hook import TeammateContext, TeamSpawnRequest, with_teammate_context
from ..agent.team_mailbox import ingest_team_mailbox
from ..compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from ..conversation import Conversation
from ..permission import Mode
from ..session import Writer
from ..tool.filter import TEAMMATE_TOOLS, FilterParams, apply_agent_tool_filter
from ..worktree import validate_slug
from .backend import SpawnRequest, new_backend
from .mailbox import Box, Message, MessageType
from .types import (
    MemberExistsError,
    TeammateInfo,
    TeamNotFoundError,
)


def team_system_prompt_suffix() -> str:
    """F39 附录：队员系统提示词追加（无变量）。"""
    return (
        "\n\nIMPORTANT: You are running as an agent in a team.\n"
        "Just writing a response in text is not visible to others\n"
        "on your team - you MUST use the TeamSendMessage tool.\n"
        "The user interacts primarily with the team lead.\n"
        "Your work is coordinated through the task system\n"
        "and teammate messaging.\n"
    )


def build_team_context_reminder(team, member_name: str, agent_id: str, worktree_path: str) -> str:
    """F40：`<team-context>` initial system reminder。"""
    roster = ", ".join(f"{m.name}({m.agent_type or 'member'})" for m in team.members)
    return (
        "<team-context>\n"
        f"team: {team.sanitized_name}\n"
        f"你的成员名: {member_name}\n"
        f"你的 agent_id: {agent_id}\n"
        f"worktree 目录: {worktree_path}\n"
        f"当前团队成员: {roster}\n"
        "</team-context>"
    )


def truncate_for_summary(prompt: str, limit: int = 80) -> str:
    """给初始任务 mailbox 消息生成 summary（5-10 词原则的宽松实现）。"""
    s = prompt.strip().replace("\n", " ")
    return s if len(s) <= limit else s[:limit].rstrip() + "..."


def _make_reader(box: Box, holder: list[str]):
    """mailbox 读闭包：agent_id 经可变容器（in-process 的 id 在 launch 后才知道）。"""

    async def _read() -> tuple[list[int], list[Any]]:
        aid = holder[0]
        if not aid:
            return [], []
        return await box.read_unread(aid)

    return _read


def _make_marker(box: Box, holder: list[str]):
    async def _mark(indices: list[int]) -> None:
        aid = holder[0]
        if not aid or not indices:
            return
        await box.mark_read(aid, indices)

    return _mark


async def spawn_teammate(mgr, req: TeamSpawnRequest) -> str:
    """F25 主流程，返回 JSON 字符串（member_name / agent_id / worktree / backend / pane_id）。"""
    team = mgr.get(req.team_name)
    if team is None:
        raise TeamNotFoundError(f"未找到团队: {req.team_name}")

    member = req.member_name or f"member-{secrets.token_hex(3)}"
    try:
        validate_slug(member)
    except ValueError:
        raise ValueError(f"非法成员名: {member}（不允许 / 或路径遍历）") from None
    if team.member_by_name(member) is not None:
        raise MemberExistsError(f"团队成员已存在: {member}")

    # 解析定义（指定 subagent_type；留空且 FORK_TEAMMATE 开 → fork；否则 general-purpose）
    catalog = req.catalog
    if req.agent_type:
        defi = catalog.resolve(req.agent_type) if catalog is not None else None
        if defi is None:
            raise ValueError(f"unknown subagent_type: {req.agent_type}")
    elif getattr(req, "fork_teammate", False):
        defi = catalog.fork_definition()
    else:
        defi = catalog.resolve("general-purpose") if catalog is not None else None
        if defi is None:
            raise ValueError("general-purpose 定义缺失")

    # 创建队员 Worktree（嵌套 slug，`/` 平化为 `+`）
    if mgr.wt_mgr is None:
        raise RuntimeError("worktree manager not configured")
    wt = await mgr.wt_mgr.create(f"team-{team.sanitized_name}/{member}", "HEAD", False)
    worktree_path = wt.path
    branch = wt.branch

    # 申请独立 session（ch12 格式 <root>/.novacode/sessions/<id>/）
    ses_ctx = new_session_context(os.getcwd())
    session_dir = ses_ctx.session_dir

    # 工具集装配（HANDOFF ch13 设计决策 2：filter 基础 + Team 协作工具）
    parent = req.parent
    reg = parent._registry
    base = apply_agent_tool_filter(
        FilterParams(
            all=[d.name for d in reg.definitions()],
            source=int(defi.source),
            background=False,
            allowed=defi.tools,
            disallowed=defi.disallowed_tools,
        )
    )
    full_allowed = base + list(TEAMMATE_TOOLS)
    plan_mode = req.plan_mode_required or defi.plan_mode_required
    if plan_mode:
        # F48：plan 队员只读基础 + Team 协作工具（不含写工具）——"先规划"靠工具集收窄
        allowed = [n for n in base if reg.get(n) is not None and reg.get(n).read_only]
        allowed = allowed + list(TEAMMATE_TOOLS)
    else:
        allowed = full_allowed

    is_inproc = str(team.backend) == "in-process"
    backend = new_backend(team.backend, task_mgr=mgr.task_mgr)
    pending_agent_id = "agent-" + secrets.token_hex(7)  # Pane 后端用；in-process 由 launch 生成

    # in-process：构造 sub_agent + conv + TeammateContext（ctx 注入 + 落盘 writer）
    sub_agent = None
    sub_conv = None
    tc = None
    if is_inproc:
        from ..agent import Agent  # 函数内延迟 import（team → agent 单向）
        from ..agent.fork import build_forked_messages
        from ..agent.permission_upgrade import default_upgrader
        from ..agent.runtime import SessionRuntime

        sub_runtime = SessionRuntime(
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=ses_ctx,
            context_window=parent.runtime.context_window,
        )
        writer = Writer(session_dir)
        sys_prompt = (defi.system_prompt or "") + team_system_prompt_suffix()
        if defi.is_fork():
            parent_msgs = parent._active_conv.messages() if parent._active_conv is not None else []
            sub_conv = Conversation.from_messages(
                build_forked_messages(parent_msgs),
                on_append=writer.on_append,
                on_replace=writer.on_replace,
            )
        else:
            sub_conv = Conversation(on_append=writer.on_append, on_replace=writer.on_replace)

        sub_agent = Agent(
            parent._provider,
            reg,
            parent._version,
            parent._engine,
            runtime=sub_runtime,
            allowed_tools=allowed,
            system_prompt=sys_prompt,
            max_turns=defi.max_turns,
            permission_mode=Mode.PLAN if plan_mode else defi.permission_mode,
            dont_ask=True,  # F39a：队员一律 dont_ask，覆盖角色定义
            approval_upgrader=default_upgrader,
            hook_engine=parent._hook_engine,
        )
        holder: list[str] = [""]
        tc = TeammateContext(
            team_name=team.sanitized_name,
            member_name=member,
            agent_id="",  # launch 后回填
            backend_type=str(team.backend),
            mailbox_dir=team.mailbox_dir,
            read_unread=_make_reader(Box(team.mailbox_dir), holder),
            mark_read=_make_marker(Box(team.mailbox_dir), holder),
        )
        # F40：<team-context> 追加进系统提示（agent_id 在 spawn 后回填）
        sys_prompt += "\n\n" + build_team_context_reminder(
            team, member, pending_agent_id, worktree_path
        )
        sub_agent._system_prompt = sys_prompt
        # F41/T32：每轮 mailbox 注入 + plan 审批切模式（闭包持 agent 引用）
        sub_agent._pre_iteration_reminders = ingest_team_mailbox(tc, sub_agent, full_allowed)

    req_spawn = SpawnRequest(
        team_name=team.sanitized_name,
        member_name=member,
        agent_id=pending_agent_id if not is_inproc else "",
        worktree_path=worktree_path,
        session_dir=session_dir,
        agent_type=req.agent_type,
        model=req.model,
        initial_prompt=req.prompt,
        plan_mode_required=plan_mode,
        sub_agent=sub_agent,
        conv=sub_conv,
        task_mgr=mgr.task_mgr,
    )
    # Pane 后端：initial_prompt 预写入 mailbox（F13），不走命令行
    if not is_inproc:
        await Box(team.mailbox_dir).write(
            pending_agent_id,
            Message(
                from_="lead",
                to=member,
                type=MessageType.TEXT,
                summary=truncate_for_summary(req.prompt),
                content=req.prompt,
            ),
        )

    # spawn（in-process 需在 with_teammate_context 内 launch，create_task 继承 ctx）
    if is_inproc and tc is not None:
        with with_teammate_context(tc):
            pane_id, agent_id = await backend.spawn(req_spawn)
        tc.agent_id = agent_id
    else:
        pane_id, agent_id = await backend.spawn(req_spawn)

    # 注册 + 写入 members（F25 step 11/12；add_member 内含 reload_from_disk_locked）
    if mgr.registry is not None:
        mgr.registry.register(member, agent_id)
    info = TeammateInfo(
        name=member,
        agent_id=agent_id,
        agent_type=req.agent_type,
        model=req.model,
        worktree_path=worktree_path,
        branch=branch,
        backend_type=team.backend,
        pane_id=pane_id,
        is_active=True,
        plan_mode_required=plan_mode,
        session_dir=session_dir,
    )
    await team.add_member(info)
    return json.dumps(
        {
            "member_name": member,
            "agent_id": agent_id,
            "worktree": worktree_path,
            "backend": str(team.backend),
            "pane_id": pane_id,
        },
        ensure_ascii=False,
    )
