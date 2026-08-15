"""TeamHook Protocol + TeamSpawnRequest + TeammateContext（ch15 T15，spec F24/F40）。

agent 包**定义**这些协议与上下文，不 import `team` 包（避免环）；`team.Manager`
**实现** `TeamHook` 并被 `AgentTool.team_hook` 注入。队员执行期间用 `with_teammate_context`
把 `TeammateContext` 注入 ctx（参照 ch14 `tool/ctx.py::with_cwd` 的 ContextVar 先例）。
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

_ctx_teammate: contextvars.ContextVar[TeammateContext | None] = contextvars.ContextVar(
    "teammate", default=None
)


class TeamHook(Protocol):
    """Agent 工具委托给 Team Manager 的接口（spec F25）。"""

    async def spawn_teammate(self, req: TeamSpawnRequest) -> str: ...
    # 判断当前 ctx 是否在队员执行上下文中；返回 (team_name, backend_type, in_process)
    def is_teammate_context(self, ctx) -> tuple[str, str, bool]: ...


@dataclass
class TeamSpawnRequest:
    """Agent 工具参数 → spawn 入参（spec F25）。"""

    team_name: str
    member_name: str = ""
    agent_type: str = ""
    model: str = ""
    plan_mode_required: bool = False
    prompt: str = ""
    fork_teammate: bool = False  # FORK_TEAMMATE feature flag（agent_tool 注入）
    # 实现便利（非 spec 字段）：调用者 Agent 引用与 subagent catalog（AgentTool 注入）
    parent: Any = None
    catalog: Any = None


class MailboxReader(Protocol):
    """agent 包对 mailbox 的只读抽象（避免 import team.mailbox）。"""

    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Any]]: ...
    async def mark_read(self, agent_id: str, indices: list[int]) -> None: ...


@dataclass
class TeammateContext:
    """队员执行上下文（ctx 注入）。`read_unread`/`mark_read` 为闭包（team 包 spawn 时构造）。"""

    team_name: str
    member_name: str
    agent_id: str
    backend_type: str  # BackendType 字符串："tmux" / "iterm2" / "in-process"
    mailbox_dir: str = ""
    read_unread: Callable[[], Awaitable[tuple[list[int], list[Any]]]] | None = None
    mark_read: Callable[[list[int]], Awaitable[None]] | None = None
    send_message_wake: Callable[[str], Awaitable[None]] | None = None


@contextmanager
def with_teammate_context(tc: TeammateContext | None):
    """在 with 块内把 ctx 的 teammate 置为 tc（spec F16/F40）。"""
    if tc is None:
        yield
        return
    token = _ctx_teammate.set(tc)
    try:
        yield
    finally:
        _ctx_teammate.reset(token)


def teammate_context_from_ctx(ctx) -> TeammateContext | None:
    """从 ctx 取当前队员上下文（None = 非队员执行上下文）。"""
    return _ctx_teammate.get()
