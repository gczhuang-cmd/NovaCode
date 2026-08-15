"""Token 估算单测：锚点 + 字符增量 / usage 合并。"""

from __future__ import annotations

import math

from novacode.compact.token import estimate_tokens, usage_anchor
from novacode.llm import Message, Usage


def test_estimate_tokens_empty():
    assert estimate_tokens(0, [], 0) == 0


def test_estimate_tokens_anchor_increment():
    m2 = Message(role="user", content="x" * 350)
    msgs = [Message(role="user", content="a"), m2]
    # anchor_msg_len=1：只算第 2 条之后（m2）的字符增量
    assert estimate_tokens(1000, msgs, 1) == 1000 + math.ceil(350 / 3.5)


def test_estimate_tokens_anchor_len_overflow_guard():
    # anchor_msg_len 超出当前消息数：tail 为空，只返回 anchor
    assert estimate_tokens(500, [Message(role="user", content="a")], 99) == 500


def test_usage_anchor_sum():
    u = Usage(input_tokens=100, output_tokens=50, cache_write=10, cache_read=20)
    assert usage_anchor(u) == 180
