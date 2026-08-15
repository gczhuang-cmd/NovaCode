"""MCP 工具适配测试：命名拼接 / 禁用字符 / 去重 / Execute 各分支（AC6/AC7/F7/F8/F10）。"""

import asyncio

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent, Tool, ToolAnnotations

import novacode.mcp.tool as tool_mod
from novacode.mcp.tool import McpTool, adapt_tool


@pytest.fixture
def fresh_state():
    """清空模块级去重 / 非 text 告警状态，隔离单测。"""
    tool_mod._known_names.clear()
    tool_mod._non_text_warn_once.clear()


def _make_tool(name="echo", description="", schema=None, read_only_hint=None) -> Tool:
    if schema is None:
        schema = {"type": "object", "properties": {}}
    kwargs: dict = {"name": name, "input_schema": schema}
    if description:
        kwargs["description"] = description
    if read_only_hint is not None:
        kwargs["annotations"] = ToolAnnotations(read_only_hint=read_only_hint)
    return Tool(**kwargs)


class StubSession:
    """注入的远端会话 stub：记录调用、返回预设结果。"""

    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[str, object]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def _result(*blocks, is_error=False) -> CallToolResult:
    return CallToolResult(content=list(blocks), is_error=is_error)


# ─────────────────────────── adapt_tool 适配 ───────────────────────────


def test_adapt_ok(fresh_state):
    t = adapt_tool("srv", _make_tool("echo"), StubSession(_result()))
    assert isinstance(t, McpTool)
    assert t.name() == "mcp__srv__echo"
    assert t._remote_name == "echo"  # 内部字段下划线前缀，不直接暴露


def test_adapt_illegal_name_skipped(fresh_state, capsys):
    t = adapt_tool("bad.name", _make_tool("echo"), StubSession(_result()))
    assert t is None
    assert "illegal characters" in capsys.readouterr().err


def test_adapt_duplicate_skipped(fresh_state, capsys):
    stub = StubSession(_result())
    assert adapt_tool("srv", _make_tool("echo"), stub) is not None
    second = adapt_tool("srv", _make_tool("echo"), stub)
    assert second is None
    assert "duplicate" in capsys.readouterr().err


def test_adapt_description_and_schema(fresh_state):
    t = adapt_tool("srv", _make_tool("echo", description="hello world"), StubSession(_result()))
    assert t.description() == "hello world"
    # 描述为空 → 兜底含 server 名与工具名（用不同工具名避免去重拦截）
    t2 = adapt_tool("srv", _make_tool("echo2"), StubSession(_result()))
    assert "srv" in t2.description() and "echo2" in t2.description()


def test_adapt_empty_schema_fallback(fresh_state):
    t = adapt_tool("srv", _make_tool(schema={}), StubSession(_result()))
    assert t.parameters() == {"type": "object"}


def test_adapt_schema_passthrough(fresh_state):
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    t = adapt_tool("srv", _make_tool(schema=schema), StubSession(_result()))
    assert t.parameters() == schema


def test_adapt_read_only(fresh_state):
    # annotations 缺失 → read_only False（None-safe）
    t = adapt_tool("srv", _make_tool(), StubSession(_result()))
    assert t.read_only is False
    # read_only_hint True → True；False → False
    t2 = adapt_tool("srv2", _make_tool(read_only_hint=True), StubSession(_result()))
    assert t2.read_only is True
    t3 = adapt_tool("srv3", _make_tool(read_only_hint=False), StubSession(_result()))
    assert t3.read_only is False


# ─────────────────────────── execute 各分支 ───────────────────────────


def test_execute_success_joins_text_blocks(fresh_state):
    stub = StubSession(_result(TextContent(text="a"), TextContent(text="b")))
    tool = adapt_tool("srv", _make_tool("echo"), stub)
    assert tool is not None
    res = asyncio.run(tool.execute('{"msg": "hi"}'))
    assert res.is_error is False
    assert res.content == "a\nb"
    assert stub.calls == [("echo", {"msg": "hi"})]  # raw JSON 已解析为 dict 传远端


def test_execute_empty_args_no_arguments(fresh_state):
    stub = StubSession(_result(TextContent(text="ok")))
    tool = adapt_tool("srv", _make_tool("echo"), stub)
    assert tool is not None
    res = asyncio.run(tool.execute(""))
    assert res.content == "ok"
    assert stub.calls == [("echo", None)]  # 空参数 → None


def test_execute_bad_json_returns_error(fresh_state):
    tool = adapt_tool("srv", _make_tool("echo"), StubSession(_result()))
    assert tool is not None
    res = asyncio.run(tool.execute("not-json"))
    assert res.is_error is True
    assert "参数解析失败" in res.content


def test_execute_remote_is_error_maps(fresh_state):
    stub = StubSession(_result(TextContent(text="boom"), is_error=True))
    tool = adapt_tool("srv", _make_tool("echo"), stub)
    assert tool is not None
    res = asyncio.run(tool.execute("{}"))
    assert res.is_error is True
    assert res.content == "boom"  # content 仍为远端 text


def test_execute_protocol_error_returns_error(fresh_state):
    class ErrSession(StubSession):
        async def call_tool(self, name, arguments):
            raise RuntimeError("连接断开")

    tool = adapt_tool("srv", _make_tool("echo"), ErrSession(_result()))
    assert tool is not None
    res = asyncio.run(tool.execute("{}"))
    assert res.is_error is True
    assert "MCP 工具调用失败" in res.content


def test_execute_timeout_returns_error(fresh_state, monkeypatch):
    class HangSession(StubSession):
        async def call_tool(self, name, arguments):
            await asyncio.Event().wait()

    monkeypatch.setattr(tool_mod, "tool_timeout", 0.2)
    tool = adapt_tool("srv", _make_tool("echo"), HangSession(_result()))
    assert tool is not None
    res = asyncio.run(tool.execute("{}"))
    assert res.is_error is True
    assert "超时" in res.content


def test_execute_non_text_dropped_and_warn_once(fresh_state, capsys):
    stub = StubSession(
        _result(
            TextContent(text="t1"),
            ImageContent(data="x", mimeType="image/png"),
            TextContent(text="t2"),
        )
    )
    tool = adapt_tool("srv", _make_tool("echo"), stub)
    assert tool is not None
    res = asyncio.run(tool.execute("{}"))
    assert res.content == "t1\nt2"  # 仅 text 块拼接
    # 同 full_name 多次调用只告警一次
    asyncio.run(tool.execute("{}"))
    assert capsys.readouterr().err.count("non-text content blocks") == 1
