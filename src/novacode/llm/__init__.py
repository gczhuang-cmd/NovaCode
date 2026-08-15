"""协议无关的 LLM 层：统一消息/流式事件类型与 Provider Protocol。

对外接口：`Provider`（typing.Protocol）、`Message`、`StreamEvent`、`new_provider(cfg)`，
以及工具调用相关的协议无关类型 `ToolCall`/`ToolResult`/`ToolDefinition`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..config import ProviderConfig


class PromptTooLongError(Exception):
    """Provider 上报上下文超出窗口时统一抛出的哨兵异常（ch08）。"""


# 消息角色——tool 回合携带工具执行结果
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。"""

    id: str  # provider 侧调用 id；回灌结果时配对
    name: str  # 工具名（注册中心按名查找）
    input: str  # 拼接完成的 JSON 参数字符串（raw JSON）


@dataclass
class ToolDelta:
    """一次工具参数增量片段（仅展示用，不参与工具执行）。"""

    index: int  # openai delta.tool_calls[i].index，多工具并发时区分
    name: str = ""  # 工具名；仅首个增量携带（delta.function.name 非空时）
    args: str = ""  # 本次新增的 arguments 片段（增量，非累计）


@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""

    tool_call_id: str  # 对应 ToolCall.id
    content: str  # 执行产出（成功内容或结构化错误文本）
    is_error: bool = False  # 是否为错误结果（F9）


@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]  # 完整 JSON Schema：type/properties/required


@dataclass
class Usage:
    """一轮请求的 token 用量（含缓存字段，F4）。"""

    input_tokens: int = 0  # 本轮请求输入（含完整历史）token 数
    output_tokens: int = 0  # 本轮响应输出 token 数
    cache_write: int = 0  # 缓存写入（Anthropic: cache_creation_input_tokens；OpenAI: 恒 0）
    cache_read: int = 0  # 缓存读取（Anthropic: cache_read_input_tokens；OpenAI: cached_tokens）
    reasoning_tokens: int = (
        0  # 本轮思考 token（openai: completion_tokens_details.reasoning_tokens）
    )


@dataclass
class System:
    """系统提示：稳定块（可缓存）与环境块（不缓存）分属不同内容块。"""

    stable: str = ""  # 可缓存：装配好的稳定系统提示
    environment: str = ""  # 不缓存：环境信息段


@dataclass
class Request:
    """协议无关的请求：承载 `Provider.stream` 全部入参。"""

    messages: list[Message] = field(default_factory=list)  # 持久对话历史（不含本轮 reminder）
    tools: list[ToolDefinition] = field(default_factory=list)  # 本轮工具集（普通=全量 / 规划=只读）
    system: System = field(default_factory=System)  # 稳定系统提示 + 环境段
    reminder: str = ""  # 本轮 system-reminder 内容（已含标签；空=不注入）


@dataclass
class Message:
    """一条对话消息（协议无关）。

    角色：user / assistant / tool；assistant 回合可带 tool_calls，tool 回合带 tool_results。
    """

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)  # 仅 assistant
    tool_results: list[ToolResult] = field(default_factory=list)  # 仅 ROLE_TOOL


@dataclass
class StreamEvent:
    """流式事件：文本增量 / 思考增量 / 工具增量 / 工具调用 / 用量 / 正常结束 / 出错（err 与 done 互斥）。"""

    text: str = ""  # 正文文本增量
    thinking: str = ""  # 思考内容增量（openai: reasoning_content；仅展示用，不进对话）
    tool_delta: list[ToolDelta] = field(default_factory=list)  # 工具参数增量片段（仅展示用）
    tool_calls: list[ToolCall] = field(default_factory=list)  # 非空：本轮模型请求执行这些工具
    usage: Usage | None = None  # 非空：本轮 token 用量，done 之前一次性发出
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    """协议无关的 provider 接口，供 TUI 统一驱动。"""

    @property
    def name(self) -> str: ...  # 状态栏左侧显示

    @property
    def model(self) -> str: ...  # 状态栏右侧显示

    def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话；由 `Request` 承载全部入参（messages / tools / system / reminder）。

        流式吐出 StreamEvent：正文文本增量、一轮结束时的 tool_calls、用量 usage（含缓存字段）、
        正常结束 done、出错 err。
        """


def new_provider(cfg: ProviderConfig) -> Provider:
    """按 cfg.protocol 构造对应的适配器。"""
    # 函数内延迟导入：适配器从本模块导入 Message/StreamEvent，模块级导入会循环引用
    from .anthropic_provider import AnthropicProvider
    from .openai_provider import OpenAIProvider

    if cfg.protocol == "anthropic":
        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        return OpenAIProvider(cfg)
    raise ValueError(f"未知协议: {cfg.protocol}")
