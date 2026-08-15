"""Agent 工具：统一入口让主 Agent 启动子 Agent（ch13 T17，spec F1~F3/F9~F11/F30）。

- 定义式（subagent_type 非空）走 `catalog.resolve`；Fork 式（留空）走 `fork_definition`；
- 前台 inline 跑 `run_to_completion`（超时 `AUTO_BACKGROUND_SECONDS` 自动切后台）；
  后台（显式 run_in_background / Fork 强制）经 `task.Manager.launch` 返回 async_launched；
- 工具过滤多层防线在子 Agent 构造时应用一次（`tool.filter.apply_agent_tool_filter`），
  主 Agent 的工具列表不受影响（spec N1/F31）。
- `model` 字段本期解析但不切换 provider（单 provider 环境，恒用父）。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass

from ..compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from ..conversation import Conversation
from ..tool import Result
from ..tool.filter import FilterParams, apply_agent_tool_filter
from .agent_worktree import _execute_with_worktree  # ch14：isolation=worktree 分支
from .fork import build_forked_messages
from .permission_upgrade import default_upgrader

# 前台 inline 子 Agent 超时秒数（超时自动切后台，spec F17-2）
AUTO_BACKGROUND_SECONDS: float = 120.0


def _make_sub_runtime(context_window: int):
    """构造子 Agent 的独立 SessionRuntime（继承主窗口阈值，避免压缩错乱）。"""
    from ..agent.runtime import SessionRuntime

    return SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(tempfile.gettempdir()),
        context_window=context_window,
    )


@dataclass
class AgentArgs:
    """Agent 工具参数（spec F1）。"""

    prompt: str = ""
    description: str = ""
    subagent_type: str = ""
    model: str = ""  # 本期解析但不切换 provider
    run_in_background: bool = False
    name: str = ""
    team_name: str = ""  # ch15：非空时走 Team spawn 分支


async def _aggregate_events(events: asyncio.Queue, partial) -> None:
    """消费前台子 Agent 事件，更新 PartialState（tool_count / last_activity / usage）。"""
    from ..agent import Phase  # 函数内延迟导入

    try:
        while True:
            item = await events.get()
            if item is None:
                break
            phase = getattr(item, "phase", None)
            if phase == Phase.START:
                partial.tool_count += 1
                partial.last_activity = getattr(item, "name", "")
            elif hasattr(item, "input") and hasattr(item, "output"):
                partial.usage.input += item.input
                partial.usage.output += item.output
    except asyncio.CancelledError:
        pass


class AgentTool:
    """注册到 tool.Registry 的统一 Agent 工具。"""

    read_only = False  # 子 Agent 可能做任何事
    is_system = False  # 普通工具：子 Agent 构造时经 ALL_AGENT_DISALLOWED_TOOLS 剔除

    def __init__(
        self,
        catalog,
        task_mgr,
        parent=None,
        bg_enabled: bool = True,
        worktree_mgr=None,  # ch14：worktree.Manager | None（None=不启用隔离）
        team_hook=None,  # ch15：team.Manager | None（None=不启用 Team spawn）
        fork_teammate: bool = False,  # ch15：FORK_TEAMMATE feature flag
    ) -> None:
        self._catalog = catalog
        self._task_mgr = task_mgr
        self._parent = parent
        self._bg_enabled = bg_enabled
        self._worktree_mgr = worktree_mgr
        self._team_hook = team_hook
        self._fork_teammate = fork_teammate

    def set_parent(self, ag) -> None:
        """tui.run 构造主 Agent 后回填 parent 引用（provider/registry/engine 等来源）。"""
        self._parent = ag

    def name(self) -> str:
        return "Agent"

    def description(self) -> str:
        names = ", ".join(d.name for d in self._catalog.list())
        return (
            "启动一个子 Agent 执行独立任务。subagent_type 指定预定义角色；"
            "留空走 Fork 路径（克隆当前对话、无条件后台）。"
            "team_name 非空时把子 Agent 派到该 Team 作为队员（Team spawn 分支）。"
            f"subagent_type 可选值: {names or '(无定义)'}"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "交给子 Agent 的任务指令",
                },
                "description": {
                    "type": "string",
                    "description": "一句话描述任务，供 UI 展示",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "预定义角色名；留空走 Fork 路径",
                },
                "model": {
                    "type": "string",
                    "description": "模型覆盖（haiku/sonnet/opus/inherit；本期不切换 provider）",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "true 时强制后台启动；Fork 路径忽略此字段（无条件后台）",
                },
                "name": {
                    "type": "string",
                    "description": "给本次启动的子 Agent 命名，供 SendMessage 用",
                },
                "team_name": {
                    "type": "string",
                    "description": "非空时把子 Agent 派到该 Team 作为队员（Team spawn 分支）",
                },
            },
            "required": ["prompt", "description"],
        }

    async def execute(self, args: str) -> Result:
        """Tool 协议：args 是 raw JSON 字符串（无 ToolContext，见 spec 修订记录）。"""
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="Agent 工具参数应为 JSON 对象")
        known = {k: data[k] for k in AgentArgs.__dataclass_fields__ if k in data}
        a_args = AgentArgs(**known)
        if not a_args.prompt:
            return Result(is_error=True, content="prompt is required")
        if not a_args.description:
            return Result(is_error=True, content="description is required")
        parent = self._parent
        if parent is None:
            return Result(is_error=True, content="Agent tool not wired to parent")

        # ch15：Team spawn 分支（team_name 非空时委托 team_hook）
        if a_args.team_name:
            return await self._execute_team(a_args, parent)

        # 解析定义
        if a_args.subagent_type:
            defi = self._catalog.resolve(a_args.subagent_type)
            if defi is None:
                return Result(
                    is_error=True, content=f"unknown subagent_type: {a_args.subagent_type}"
                )
        else:
            defi = self._catalog.fork_definition()

        # 决定后台（Fork 无条件后台）
        background = defi.background or a_args.run_in_background or defi.is_fork()
        if defi.isolation == "worktree":
            background = (
                False  # ch14 F23：isolation 强制前台（忽略 background 字段与 run_in_background）
            )
        if background and not self._bg_enabled:
            return Result(
                is_error=True,
                content="background mode is disabled by config (enableSubAgentBackground=false)",
            )

        # 工具过滤多层防线（构造期唯一一次；主 Agent 工具列表不变）
        allowed = apply_agent_tool_filter(
            FilterParams(
                all=[d.name for d in parent._registry.definitions()],
                source=int(defi.source),
                background=background,
                allowed=defi.tools,
                disallowed=defi.disallowed_tools,
            )
        )

        # 构造子 Agent（共享父 provider/registry/engine/hook_engine；独立 runtime）
        from ..agent import Agent  # 函数内延迟导入

        sub_runtime = _make_sub_runtime(parent.runtime.context_window)
        sub_agent = Agent(
            parent._provider,
            parent._registry,
            parent._version,
            parent._engine,
            runtime=sub_runtime,
            allowed_tools=allowed,
            system_prompt=defi.system_prompt,
            max_turns=defi.max_turns,
            permission_mode=defi.permission_mode,
            dont_ask=defi.dont_ask,
            approval_upgrader=default_upgrader,  # 本期默认拒绝（spec 修订记录）
            hook_engine=parent._hook_engine,
        )

        # 子 conv：定义式空白 / Fork 克隆主对话（任务文本由 run_to_completion add_user）
        if defi.is_fork():
            parent_msgs = self._parent_messages(parent)
            sub_conv = Conversation.from_messages(build_forked_messages(parent_msgs))
        else:
            sub_conv = Conversation()

        # 后台路径
        if background:
            task_id = await self._task_mgr.launch(sub_agent, sub_conv, a_args.name, a_args.prompt)
            return Result(content=json.dumps({"task_id": task_id, "status": "async_launched"}))

        # 前台路径（超时自动切后台，**不 cancel 协程**——wait_for 超时会 cancel 子 Agent，
        # 导致重跑时 conv 状态被中断污染、result 为空；用 asyncio.wait 超时后让协程继续跑，
        # adopt 直接接管同一个 run_task）
        from ..task.manager import PartialState

        events: asyncio.Queue = asyncio.Queue(maxsize=64)
        partial = PartialState()
        aggregator = asyncio.create_task(_aggregate_events(events, partial))
        if defi.isolation == "worktree":  # ch14：隔离分支（创建临时 Worktree + ctx cwd 注入）
            if self._worktree_mgr is None:
                return Result(is_error=True, content="worktree manager not configured")
            run_task = asyncio.create_task(
                _execute_with_worktree(
                    self._worktree_mgr, defi, sub_agent, sub_conv, a_args.prompt, events
                )
            )
        else:
            run_task = asyncio.create_task(
                sub_agent.run_to_completion(sub_conv, a_args.prompt, events)  # type: ignore[attr-defined]
            )
        try:
            done, _ = await asyncio.wait([run_task], timeout=AUTO_BACKGROUND_SECONDS)
            if run_task in done:
                # 正常完成（run_task.result() 抛 MaxTurnsReached 等异常时转错误）
                return Result(content=run_task.result())
            # 超时：run_task 继续在后台跑，adopt 接管（不 cancel、不重跑）
            task_id = await self._task_mgr.adopt_running(
                sub_agent, sub_conv, a_args.name, events, run_task, partial
            )
            return Result(
                content=json.dumps({"task_id": task_id, "status": "timed_out_to_background"})
            )
        except Exception as e:  # noqa: BLE001 子 Agent 任何错误转结构化结果
            return Result(is_error=True, content=f"subagent error: {e}")
        finally:
            # 前台聚合器收尾；超时切后台时 events 队列交给 manager 接管，不能 put(None) 触发退出
            aggregator.cancel()

    # ── 内部 ─────────────────────────────────────────────

    async def _execute_team(self, a_args: AgentArgs, parent) -> Result:
        """ch15 F25：team_name 非空时委托 team_hook.spawn_teammate。"""
        if self._team_hook is None:
            return Result(is_error=True, content="team 功能未启用（team_hook 未注入）")
        _, _, in_proc = self._team_hook.is_teammate_context(None)
        if in_proc:
            return Result(
                is_error=True,
                content="in-process 队员不能嵌套 spawn Team 成员（InProcessTeammateNoSpawnError）",
            )
        from .team_hook import TeamSpawnRequest  # 函数内延迟 import（避免顶层环）

        try:
            text = await self._team_hook.spawn_teammate(
                TeamSpawnRequest(
                    team_name=a_args.team_name,
                    member_name=a_args.name,
                    agent_type=a_args.subagent_type,
                    model=a_args.model,
                    prompt=a_args.prompt,
                    parent=parent,
                    catalog=self._catalog,
                    fork_teammate=self._fork_teammate,
                )
            )
        except Exception as e:  # noqa: BLE001 结构化错误返回
            return Result(is_error=True, content=f"spawn team member error: {e}")
        return Result(content=text)

    def _parent_messages(self, parent) -> list:
        """取主对话历史（Fork 路径）。parent._active_conv 由 Agent.run 在每轮开头记录。"""
        conv = getattr(parent, "_active_conv", None)
        return conv.messages() if conv is not None else []
