"""SubAgent 工具过滤多层防线（ch13 T8，spec F26~F31；ch15 扩展 Team 协作工具可见性）。

边界：`ALL_AGENT_DISALLOWED_TOOLS` 约束 SubAgent 构造路径（Agent 工具 / launch_fork 调
apply_agent_tool_filter 时）。ch15 的 5 个 Team 协作工具注册进默认 registry（队员
`definitions_filtered` 按名解析需要），但并入 `ALL_AGENT_DISALLOWED_TOOLS` 使普通子 Agent
默认看不到；队员工具集由 Team 模块**装配**（`apply_agent_tool_filter(基础) + TEAMMATE_TOOLS`，
不扩展 FilterParams 的 teammate 字段）——与 HANDOFF ch13 设计决策 2「注册进去，不是过滤留下的」一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ch15：Team 协作工具（队员装配用）。并入 ALL_AGENT_DISALLOWED_TOOLS 使普通子 Agent 不可见。
TEAMMATE_TOOLS: list[str] = [
    "TeamTaskCreate",
    "TeamTaskGet",
    "TeamTaskList",
    "TeamTaskUpdate",
    "TeamSendMessage",
]

# 任何子 Agent 默认不能用的元工具名列表（spec F26）。
# 子 Agent 不能嵌套（Agent）、不能操控主 Agent 的后台任务 / 给别的后台 Agent 发指令
# （TaskList/TaskGet/TaskStop/SendMessage）；Team 协作工具默认不可见（队员装配时加回）。
ALL_AGENT_DISALLOWED_TOOLS: list[str] = [
    "Agent",
    "TaskList",
    "TaskGet",
    "TaskStop",
    "SendMessage",
    *TEAMMATE_TOOLS,
]

# 自定义（user / project / plugin 来源）Agent 比内置 Agent 多禁用的工具。本期为空（接口预留，F27）。
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = []

# 后台 Agent 工具白名单（spec F28）：基础读写网络工具。
# 不含任何元工具；MCP 工具按 mcp__ 前缀动态识别放行。
ASYNC_AGENT_ALLOWED_TOOLS: list[str] = [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "bash",
    "load_skill",
    "install_skill",
]


@dataclass
class FilterParams:
    """apply_agent_tool_filter 的入参（spec F30 顺序过滤）。"""

    all: list[str]  # registry 的全部工具名（按注册顺序）
    source: int  # subagent.Source 的整数值（0=builtin,1=user,2=project,3=plugin）
    background: bool  # 是否为后台子 Agent（Fork 恒 True）
    allowed: list[str] = field(default_factory=list)  # Agent 定义 tools 白名单；空=不收窄
    disallowed: list[str] = field(default_factory=list)  # Agent 定义 disallowedTools 黑名单


def is_mcp_or_skill(name: str) -> bool:
    """后台白名单的动态识别：mcp__ 前缀工具放行。"""
    return name.startswith("mcp__")


def apply_agent_tool_filter(p: FilterParams) -> list[str]:
    """按 spec F30 顺序过滤，返回最终 allowed 列表（传给 Agent(allowed_tools=...)）。

    1. 起点 = 全部工具名
    2. 去 ALL_AGENT_DISALLOWED_TOOLS（元工具）
    3. 非内置来源去 CUSTOM_AGENT_DISALLOWED_TOOLS（本期为空）
    4. 后台 → 与 ASYNC_AGENT_ALLOWED_TOOLS + mcp__ 取交集
    5. 去 disallowed（定义黑名单）
    6. allowed 非空 → 取交集（定义白名单）
    """
    result = [n for n in p.all if n not in ALL_AGENT_DISALLOWED_TOOLS]
    if p.source >= 1:  # Source.USER 及以上
        result = [n for n in result if n not in CUSTOM_AGENT_DISALLOWED_TOOLS]
    if p.background:
        async_allowed = set(ASYNC_AGENT_ALLOWED_TOOLS)
        result = [n for n in result if n in async_allowed or is_mcp_or_skill(n)]
    result = [n for n in result if n not in p.disallowed]
    if p.allowed:
        allow = set(p.allowed)
        result = [n for n in result if n in allow]
    return result
