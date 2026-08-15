"""Fork 辅助函数测试（ch13 T13）。"""

from __future__ import annotations

from novacode.agent.fork import (
    FORK_BOILERPLATE,
    FORK_BOILERPLATE_TAG,
    build_forked_messages,
    is_fork_context,
)
from novacode.llm import Message, ToolCall, ToolResult


def _assistant_with_calls(ids: list[str], text: str = "think") -> Message:
    return Message(
        role="assistant",
        content=text,
        tool_calls=[ToolCall(id=i, name="read_file", input='{"path": "x"}') for i in ids],
    )


def test_empty_parent():
    msgs = build_forked_messages([])
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == FORK_BOILERPLATE


def test_parent_with_paired_tool_calls():
    parent = [
        Message(role="user", content="q"),
        _assistant_with_calls(["c1"]),
        Message(
            role="tool",
            tool_results=[ToolResult(tool_call_id="c1", content="ok", is_error=False)],
        ),
        Message(role="assistant", content="done"),
    ]
    msgs = build_forked_messages(parent)
    # 深拷贝：4 条父消息 + 1 条 Boilerplate user
    assert len(msgs) == 5
    assert msgs[-1].content == FORK_BOILERPLATE
    # 前 4 条与父一致（内容深拷贝）
    assert msgs[0].content == "q"
    assert msgs[3].content == "done"
    # 不新增 placeholder（已配对）
    assert sum(1 for m in msgs if m.role == "tool") == 1


def test_parent_with_orphan_tool_calls():
    parent = [
        Message(role="user", content="q"),
        _assistant_with_calls(["c1", "c2"]),
    ]
    msgs = build_forked_messages(parent)
    # 2 条父 + 1 条 placeholder tool + 1 条 Boilerplate user
    assert len(msgs) == 4
    assert msgs[2].role == "tool"
    assert len(msgs[2].tool_results) == 2
    assert {r.tool_call_id for r in msgs[2].tool_results} == {"c1", "c2"}
    assert all(r.is_error for r in msgs[2].tool_results)
    assert all(r.content == "[forked, skipped]" for r in msgs[2].tool_results)
    assert msgs[-1].content == FORK_BOILERPLATE


def test_orphan_only_missing_ones():
    parent = [
        Message(role="user", content="q"),
        _assistant_with_calls(["c1", "c2"]),
        Message(
            role="tool",
            tool_results=[ToolResult(tool_call_id="c1", content="ok", is_error=False)],
        ),
    ]
    msgs = build_forked_messages(parent)
    # 3 条父 + 1 条 placeholder（只补 c2）+ 1 条 Boilerplate
    assert len(msgs) == 5
    placeholder = msgs[3]
    assert placeholder.role == "tool"
    assert [r.tool_call_id for r in placeholder.tool_results] == ["c2"]


def test_no_mutation_of_parent():
    parent = [
        Message(role="user", content="q"),
        _assistant_with_calls(["c1"]),
    ]
    build_forked_messages(parent)
    # 父消息未被篡改
    assert len(parent) == 2
    assert parent[1].tool_calls[0].id == "c1"


def test_is_fork_context_true():
    msgs = [
        Message(role="user", content="plain"),
        Message(role="user", content=f"{FORK_BOILERPLATE_TAG}\n任务"),
    ]
    assert is_fork_context(msgs) is True


def test_is_fork_context_false():
    msgs = [Message(role="user", content="plain"), Message(role="user", content="hi")]
    assert is_fork_context(msgs) is False


def test_is_fork_context_empty():
    assert is_fork_context([]) is False
