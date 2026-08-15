"""manage_context 主入口：编排第 1/2 层、自动 / 手动 / 紧急三路径、写回 Conversation。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from ..conversation import Conversation
from ..llm import Provider, ToolDefinition
from .const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE
from .layer1 import offload_and_snip
from .layer2 import auto_compact, force_compact
from .state import CompactCircuitBreaker, ContentReplacementState, RecoveryState, SessionContext
from .token import estimate_tokens

logger = logging.getLogger(__name__)


class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    """manage_context 的入参：状态、锚点、估算与触发类型。"""

    conv: Conversation
    provider: Provider
    model: str
    context_window: int
    tool_defs: list[ToolDefinition]
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int
    anchor_msg_len: int
    estimated_token: int
    trigger: TriggerKind


@dataclass
class ManageOutput:
    before_tokens: int
    after_tokens: int


async def manage_context(in_: ManageInput) -> ManageOutput:
    """Agent 每轮请求前必调的唯一入口（F6/F21/F25）。

    - MANUAL：跳过第 1 层、阈值、熔断，直接 force_compact（F22）；
    - EMERGENCY：先强制跑一次 offload_and_snip，再 force_compact（F25）；
    - AUTO：先第 1 层，用 layer1 之后的消息重算估算 token，低于阈值或熔断则只 layer1 生效。
    """
    if in_.trigger == TriggerKind.MANUAL:
        new_msgs, before, after = await force_compact(in_)
        in_.conv.replace_history(new_msgs)
        return ManageOutput(before_tokens=before, after_tokens=after)

    if in_.trigger == TriggerKind.EMERGENCY:
        layer1_out = offload_and_snip(in_.conv.messages(), in_.replacement, in_.session)
        # layer1 渐进替换不写 compact 标记（F12，仅 layer2 摘要后写，避免每轮膨胀）
        in_.conv.replace_history(layer1_out, notify=False)
        new_msgs, before, after = await force_compact(in_)
        in_.conv.replace_history(new_msgs)
        return ManageOutput(before_tokens=before, after_tokens=after)

    # AUTO
    layer1_out = offload_and_snip(in_.conv.messages(), in_.replacement, in_.session)
    # 无论是否触发 layer2 都必须写回内存（layer1 替换生效）；但 layer1 不写盘标记
    in_.conv.replace_history(layer1_out, notify=False)
    est = estimate_tokens(in_.usage_anchor, layer1_out, in_.anchor_msg_len)
    if in_.context_window <= SUMMARY_RESERVE + AUTO_SAFETY_MARGIN:
        # sanity check：阈值非正会导致每轮都触发摘要死循环（plan 技术决策）
        logger.warning(
            "context_window 过小（≤ %d），跳过自动 layer2", SUMMARY_RESERVE + AUTO_SAFETY_MARGIN
        )
        return ManageOutput(before_tokens=in_.estimated_token, after_tokens=est)
    threshold = in_.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
    if est < threshold or in_.auto_tracking.tripped():
        return ManageOutput(before_tokens=in_.estimated_token, after_tokens=est)
    new_msgs, before, after = await auto_compact(in_)
    in_.conv.replace_history(new_msgs)
    return ManageOutput(before_tokens=before, after_tokens=after)
