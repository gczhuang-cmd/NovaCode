"""子 Agent 审批升级回调类型（ch13 T11，spec F13）。

子 Agent 的工具 Ask 触发时调 `approval_upgrader(req)`。默认实现返回「拒绝本次」：
子 Agent 收到拒绝原因后自行调整（换工具 / 说明无法执行）。本期不做主 TUI 弹窗
（前台 inline 子 Agent 阻塞在主 Agent 工具执行内，事件流暂停无法弹窗，见 spec 修订记录）。

`ApprovalRequestLike` 是结构协议：`agent/__init__.py` 的 `ApprovalRequest` dataclass
（name/args/reason/respond）天然满足，避免本模块 import agent 造成循环。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..permission import Outcome


class ApprovalRequestLike(Protocol):
    """ApprovalRequest 的结构最小接口（name/args/reason 供展示与拒绝文案）。"""

    name: str
    args: str
    reason: str


ApprovalUpgrader = Callable[
    [ApprovalRequestLike],
    Awaitable[tuple[Outcome, bool]],
]


async def default_upgrader(req: ApprovalRequestLike) -> tuple[Outcome, bool]:
    """默认实现：拒绝本次。子 Agent 的 Ask 工具被拒，原因回灌让它调整。"""
    return Outcome.DENY_ONCE, True
