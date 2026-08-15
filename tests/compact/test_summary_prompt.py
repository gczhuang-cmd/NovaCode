"""摘要 Prompt 单测：结构断言 / 序列化确定性 / <summary> 解析。"""

from __future__ import annotations

from novacode.compact.summary_prompt import (
    build_summary_prompt,
    extract_summary,
    serialize_conversation,
)
from novacode.llm import Message, ToolCall, ToolResult


def test_build_summary_prompt_shape():
    out = build_summary_prompt([Message(role="user", content="hi")])
    assert len(out) == 1 and out[0].role == "user"
    content = out[0].content
    for needle in (
        "<analysis>",
        "<summary>",
        "## 1 主要请求和意图",
        "## 6 所有用户消息原文",
        "不要调用任何工具",
    ):
        assert needle in content


def test_serialize_conversation_deterministic():
    msgs = [
        Message(role="user", content="u1"),
        Message(
            role="assistant",
            content="a1",
            tool_calls=[ToolCall(id="c1", name="read_file", input='{"path":"x"}')],
        ),
        Message(
            role="tool",
            tool_results=[ToolResult(tool_call_id="c1", content="r1", is_error=False)],
        ),
    ]
    s1 = serialize_conversation(msgs)
    s2 = serialize_conversation(msgs)
    assert s1 == s2
    assert "[call read_file id=c1" in s1
    assert "[result id=c1" in s1


def test_extract_summary():
    assert extract_summary("pre<summary>正文</summary>post") == "正文"
    assert extract_summary("no tags") == "no tags"
    assert extract_summary("<summary>a</summary><summary>b</summary>") == "b"
