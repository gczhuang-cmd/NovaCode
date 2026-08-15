"""Token 估算：锚定真实 usage + 字符增量（F13/F14，纯函数）。"""

from __future__ import annotations

import math

from ..llm import Message, Usage
from .const import ESTIMATE_CHARS_PER_TOKEN


def usage_anchor(u: Usage) -> int:
    """把 stream 尾事件的 usage 合并成单一锚点值（input + output + cache_read + cache_write）。"""
    return u.input_tokens + u.output_tokens + u.cache_read + u.cache_write


def message_chars(msgs: list[Message]) -> int:
    """计算单段消息列表的字符总量（UTF-8 字节口径，含 tool_calls / tool_results）。"""
    total = 0
    for m in msgs:
        total += len(m.content.encode("utf-8"))
        for c in m.tool_calls:
            total += len(c.input.encode("utf-8"))
        for r in m.tool_results:
            total += len(r.content.encode("utf-8"))
    return total


def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    """锚定最近一次 provider usage + 之后新增消息的字符增量。

    - `anchor`：上一次主对话路径 stream 真实 usage 之和；
    - `anchor_msg_len`：anchor 记录时 conv.length()，只对锚点之后追加的消息算增量，
      避免把已含在 anchor 里的历史重复计算；
    - 返回 `anchor + math.ceil(message_chars(tail) / ESTIMATE_CHARS_PER_TOKEN)`。
    """
    start = max(0, min(anchor_msg_len, len(all_msgs)))
    tail = all_msgs[start:]
    return anchor + math.ceil(message_chars(tail) / ESTIMATE_CHARS_PER_TOKEN)
