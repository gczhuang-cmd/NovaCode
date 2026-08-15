"""第 2 层纯函数单测：近期原文边界 / 配对修正 / 分组 / 拼接。"""

from __future__ import annotations

from novacode.compact.layer2 import _join_after_summary, group_by_user_turn, pick_recent_tail
from novacode.llm import Message, ToolCall, ToolResult


def test_group_by_user_turn():
    msgs = [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1"),
        Message(role="tool", content=""),
        Message(role="user", content="u2"),
        Message(role="assistant", content="a2"),
    ]
    groups = group_by_user_turn(msgs)
    assert len(groups) == 2
    assert len(groups[0]) == 3 and len(groups[1]) == 2


def test_pick_recent_tail_short_returns_all():
    msgs = [Message(role="user", content=f"m{i}") for i in range(6)]
    assert len(pick_recent_tail(msgs)) == 6  # token 下界未达 → 全部保留


def test_pick_recent_tail_pair_fix():
    msgs = [
        Message(role="user", content="u" * 4000),
        Message(
            role="assistant",
            content="a",
            tool_calls=[ToolCall(id="c1", name="read_file", input="{}")],
        ),
        Message(
            role="tool",
            tool_results=[ToolResult(tool_call_id="c1", content="r" * 4000, is_error=False)],
        ),
        Message(role="user", content="u2" * 3000),
        Message(role="assistant", content="a2"),
    ]
    tail = pick_recent_tail(msgs)
    assert tail[0].role in ("user", "assistant")
    assert tail[0].role != "tool"  # 不落在落单 tool_result 上


def test_join_after_summary_avoids_consecutive_user():
    summary = Message(role="user", content="S")
    recent = [Message(role="user", content="r")]
    out = _join_after_summary(summary, recent)
    assert [m.role for m in out] == ["user", "assistant", "user"]


def test_join_after_summary_empty_recent():
    summary = Message(role="user", content="S")
    assert _join_after_summary(summary, []) == [summary]
