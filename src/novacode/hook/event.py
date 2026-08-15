"""生命周期事件定义：11 个 Event 枚举 + 拦截类判定（ch12 F9）。"""

from __future__ import annotations

import enum


class Event(str, enum.Enum):
    """Hook 生命周期事件（枚举值对应 YAML 字面量，反查用 `Event(s)`）。"""

    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    SESSION_RESUME = "SessionResume"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    PRE_USER_MESSAGE = "PreUserMessage"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"


# 拦截类事件：可通过动作表达「拒绝」信号（shell exit 2 / http decision=block）。
# 不允许 async（async 无法同步表达拦截结果），加载期校验（F28）。
BLOCKING_EVENTS: frozenset[Event] = frozenset({Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT})


def is_blocking(e: Event) -> bool:
    """事件是否拦截类（可表达拒绝）。"""
    return e in BLOCKING_EVENTS


def parse_event(s: str) -> Event | None:
    """按 YAML 字面量反查 Event；未知返回 None。"""
    try:
        return Event(s)
    except ValueError:
        return None
