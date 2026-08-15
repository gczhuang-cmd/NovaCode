"""工具过滤多层防线测试（ch13 T9）。"""

from __future__ import annotations

from novacode.tool.filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    FilterParams,
    apply_agent_tool_filter,
    is_mcp_or_skill,
)

ALL = [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "bash",
    "load_skill",
    "install_skill",
    "Agent",
    "TaskList",
    "TaskGet",
    "TaskStop",
    "SendMessage",
    "mcp__filesystem",
    "mcp__search",
]


def test_constants():
    # ch15：ALL 并入 TEAMMATE_TOOLS（普通子 Agent 默认看不到协作工具）
    assert ALL_AGENT_DISALLOWED_TOOLS == [
        "Agent",
        "TaskList",
        "TaskGet",
        "TaskStop",
        "SendMessage",
        "TeamTaskCreate",
        "TeamTaskGet",
        "TeamTaskList",
        "TeamTaskUpdate",
        "TeamSendMessage",
    ]
    assert CUSTOM_AGENT_DISALLOWED_TOOLS == []
    assert set(ASYNC_AGENT_ALLOWED_TOOLS) == {
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "bash",
        "load_skill",
        "install_skill",
    }


def test_default_removes_meta_tools():
    out = apply_agent_tool_filter(FilterParams(all=ALL, source=0, background=False))
    assert "Agent" not in out
    assert "TaskList" not in out
    assert "SendMessage" not in out
    assert "read_file" in out
    assert "mcp__filesystem" in out


def test_background_intersects_async_allowed():
    out = apply_agent_tool_filter(FilterParams(all=ALL, source=0, background=True))
    # 基础工具 + mcp__ 保留
    assert "read_file" in out
    assert "bash" in out
    assert "mcp__filesystem" in out
    # 元工具与不在白名单的工具被剔除
    assert "Agent" not in out
    assert "TaskList" not in out
    assert "SendMessage" not in out


def test_disallowed_blacklist():
    out = apply_agent_tool_filter(
        FilterParams(all=ALL, source=0, background=False, disallowed=["bash"])
    )
    assert "bash" not in out
    assert "read_file" in out


def test_allowed_whitelist():
    out = apply_agent_tool_filter(
        FilterParams(all=ALL, source=0, background=False, allowed=["read_file", "grep"])
    )
    assert out == ["read_file", "grep"]


def test_black_and_white_combined():
    # 白名单先收窄，黑名单再剔除
    out = apply_agent_tool_filter(
        FilterParams(
            all=ALL,
            source=0,
            background=False,
            allowed=["read_file", "grep", "bash"],
            disallowed=["bash"],
        )
    )
    assert out == ["read_file", "grep"]


def test_whitelist_never_reintroduces_meta():
    # 白名单写 Agent 也不能复活元工具（第一步已剔除）
    out = apply_agent_tool_filter(
        FilterParams(all=ALL, source=0, background=False, allowed=["Agent", "read_file"])
    )
    assert out == ["read_file"]


def test_source_custom_disallowed_empty():
    # 本期 CUSTOM 为空：user/project 来源行为与 builtin 一致
    out = apply_agent_tool_filter(FilterParams(all=ALL, source=2, background=False))
    assert "read_file" in out


def test_is_mcp_or_skill():
    assert is_mcp_or_skill("mcp__filesystem")
    assert is_mcp_or_skill("mcp__x")
    assert not is_mcp_or_skill("read_file")
    assert not is_mcp_or_skill("mcp")
