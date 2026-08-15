"""第 2 层：LLM 全量摘要 + PTL 自重试 + 熔断（F7~F12、F25~F29）。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..llm import Message, PromptTooLongError, Request
from .const import (
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
    RECENT_KEEP_TOKENS,
)
from .recovery import build_recovery_attachment
from .summary_prompt import build_summary_prompt, extract_summary
from .token import ESTIMATE_CHARS_PER_TOKEN, estimate_tokens, message_chars

if TYPE_CHECKING:
    from .compact import ManageInput


def group_by_user_turn(msgs: list[Message]) -> list[list[Message]]:
    """按 F27 的"用户提交 → 一组 assistant/tool 往返"分组。"""
    groups: list[list[Message]] = []
    current: list[Message] = []
    for m in msgs:
        if m.role == "user" and current:
            groups.append(current)
            current = []
        current.append(m)
    if current:
        groups.append(current)
    return groups


def pick_recent_tail(msgs: list[Message]) -> list[Message]:
    """从尾部倒序保留近期原文：累计 token ≥ RECENT_KEEP_TOKENS **且**条数 ≥ RECENT_KEEP_MESSAGES
    （两个下界都满足后才停手，F11"择宽"）；再做 tool_use/tool_result 配对修正（F12）。
    """
    if not msgs:
        return []
    total_tokens = 0.0
    total_msgs = 0
    start = len(msgs)
    for i in range(len(msgs) - 1, -1, -1):
        start = i
        total_msgs = len(msgs) - i  # 已处理的条数（倒序）
        total_tokens += message_chars([msgs[i]]) / ESTIMATE_CHARS_PER_TOKEN
        if total_tokens >= RECENT_KEEP_TOKENS and total_msgs >= RECENT_KEEP_MESSAGES:
            break
    # 配对修正：起点若是落单的 tool_result，前推到最近一条带 tool_calls 的 assistant（F12）
    if start < len(msgs) and msgs[start].role == "tool":
        prev = start - 1
        while prev >= 0 and not (msgs[prev].role == "assistant" and msgs[prev].tool_calls):
            prev -= 1
        if prev >= 0:
            start = prev
    return list(msgs[start:])


def _join_after_summary(summary_and_recovery: Message, recent: list[Message]) -> list[Message]:
    """把摘要+恢复消息与近期原文拼接，避免 user/user 连续违反 anthropic 协议。"""
    if not recent:
        return [summary_and_recovery]
    out = [summary_and_recovery]
    recent_list = list(recent)
    if recent_list[0].role == "user":
        out.append(Message(role="assistant", content="（已加载上下文摘要与恢复信息。请继续。）"))
    elif recent_list[0].role == "tool":
        # 防御：pick_recent_tail 已前推；这里再强行前移到第一条非 tool
        idx = 0
        while idx < len(recent_list) and recent_list[idx].role == "tool":
            idx += 1
        recent_list = recent_list[idx:]
        if not recent_list:
            return out
        if recent_list[0].role == "user":
            out.append(
                Message(role="assistant", content="（已加载上下文摘要与恢复信息。请继续。）")
            )
    out.extend(recent_list)
    return out


async def summarize_once(in_: ManageInput, msgs: list[Message]) -> str:
    """发一次摘要请求（无工具，F8）；PTL 异常向上抛（由调用方切到 ptl_retry）。"""
    req = Request(messages=build_summary_prompt(msgs), tools=None)
    buf: list[str] = []
    async for ev in in_.provider.stream(req):
        if ev.err is not None:
            raise ev.err
        if ev.text:
            buf.append(ev.text)
        # ev.usage：捕获但不回写 SessionRuntime.usage_anchor（摘要请求不更新主对话锚点）
    return extract_summary("".join(buf))


async def ptl_retry(in_: ManageInput, msgs: list[Message], first_err: Exception) -> str:
    """摘要请求自身 PTL 的丢消息组重试（F27）：前 N 次丢最旧 1 组，之后按比例丢。"""
    groups = group_by_user_turn(msgs)
    last_err = first_err
    retry_count = 0
    while groups:
        if retry_count < PTL_RETRY_LIMIT:
            groups = groups[1:]
        else:
            drop = max(1, math.ceil(len(groups) * PTL_DROP_PERCENTAGE))
            groups = groups[drop:]
        if not groups:
            break
        flat = [m for g in groups for m in g]
        retry_count += 1
        try:
            return await summarize_once(in_, flat)
        except PromptTooLongError as e:
            last_err = e
        # 非 PTL 异常：立即上抛，不再重试
    raise last_err


async def run_summary(in_: ManageInput) -> list[Message]:
    """两条路径共同核心：摘要 + 恢复三段 + 近期原文拼接（F15~F18）。"""
    old_msgs = in_.conv.messages()
    # 入口一次性拍快照，避免渲染期间 record_file 写入造成漂移
    recovery_snapshot = in_.recovery.snapshot()
    try:
        summary_text = await summarize_once(in_, old_msgs)
    except PromptTooLongError as first_err:
        summary_text = await ptl_retry(in_, old_msgs, first_err)
    recovery_text = build_recovery_attachment(recovery_snapshot, in_.tool_defs)
    combined = "## 历史会话摘要\n" + summary_text + "\n\n" + recovery_text
    summary_and_recovery = Message(role="user", content=combined)
    recent = pick_recent_tail(old_msgs)
    return _join_after_summary(summary_and_recovery, recent)


async def auto_compact(in_: ManageInput) -> tuple[list[Message], int, int]:
    """自动路径：整轮失败（含 PTL 重试用光）计入熔断，成功清零（F28）。"""
    before_tok = in_.estimated_token
    try:
        new_msgs = await run_summary(in_)
    except Exception:
        in_.auto_tracking.record_failure()
        raise
    in_.auto_tracking.record_success()
    after_tok = estimate_tokens(0, new_msgs, 0)
    return new_msgs, before_tok, after_tok


async def force_compact(in_: ManageInput) -> tuple[list[Message], int, int]:
    """手动 / 紧急路径：跳过熔断器，失败也不计入（F29）。"""
    before_tok = in_.estimated_token
    new_msgs = await run_summary(in_)
    after_tok = estimate_tokens(0, new_msgs, 0)
    return new_msgs, before_tok, after_tok
