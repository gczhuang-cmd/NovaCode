"""MCP 工具适配：把远端 `mcp.types.Tool` 包装成 novacode `Tool` 协议。

对外接口：
- `McpTool`：实现 `novacode.tool.Tool` 协议（`name()`/`description()`/`parameters()`/
  `read_only`/`execute`），持 `CallerSession` 供单测注入 stub。
- `adapt_tool(server_name, t, session) -> McpTool | None`：命名拼接 + 禁用字符校验 +
  全名去重。

协议要求 `name()`/`description()`/`parameters()` 是**方法**、`read_only` 是 property、
`execute(args)` 收 **raw JSON 字符串**——故内部字段一律下划线前缀，不与协议方法同名。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Any, Protocol

from mcp.types import TextContent

from ..tool import Result

# 工具全名的合法字符（LLM 工具名禁用字符之外的集合，F8）
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# 跨 server 已注册工具全名（去重：`Registry.register` 对重名抛 ValueError，在此拦截）
_known_names: set[str] = set()
# 已告警过「返回非 text 内容块」的工具全名（per full_name 限一次，F7）
_non_text_warn_once: set[str] = set()

# 单次 call_tool 超时（模块级非常量，便于单测临时改小；生产 30s，F10 内置不可配）
tool_timeout: float = 30.0


class CallerSession(Protocol):
    """远端会话的调用面：单测注入 stub，生产为 mcp.ClientSession。"""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> Any: ...


class McpTool:
    """实现 novacode `Tool` 协议；内部字段一律下划线前缀。"""

    def __init__(
        self,
        full_name: str,
        remote_name: str,
        description: str,
        parameters: dict[str, Any],
        read_only: bool,
        caller: CallerSession,
    ) -> None:
        self._full_name = full_name  # "mcp__<server>__<tool>"
        self._remote_name = remote_name  # server 上的原始工具名
        self._description = description
        self._parameters = parameters  # JSON Schema 透传
        self._read_only = read_only  # 仅来自远端 annotations.read_only_hint is True
        self._caller = caller

    is_system = False  # ch11 T14：MCP 工具非系统工具

    def name(self) -> str:
        return self._full_name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, args: str) -> Result:
        """执行远端工具：args 为 raw JSON 字符串，先解析再传远端（F7/F10）。"""
        try:
            data = json.loads(args or "{}") if args else {}
        except json.JSONDecodeError:
            return Result(content="MCP 工具参数解析失败", is_error=True)
        try:
            result = await asyncio.wait_for(
                self._caller.call_tool(self._remote_name, data or None),
                timeout=tool_timeout,
            )
        except TimeoutError:
            return Result(content="MCP 工具调用超时 (30s)", is_error=True)
        except Exception as e:  # noqa: BLE001 协议错转结构化错误回灌，不中断 Loop（F7/N5）
            return Result(content=f"MCP 工具调用失败: {e}", is_error=True)
        texts: list[str] = []
        non_text = 0
        for block in result.content:
            if isinstance(block, TextContent):
                texts.append(block.text)
            else:  # image / audio / resource_link / embedded_resource 等静默丢弃
                non_text += 1
        if non_text and self._full_name not in _non_text_warn_once:
            _non_text_warn_once.add(self._full_name)
            print(
                f"[mcp] warn: tool {self._full_name} returned non-text content blocks (dropped)",
                file=sys.stderr,
            )
        return Result(content="\n".join(texts), is_error=bool(result.is_error))


def adapt_tool(server_name: str, t: Any, session: CallerSession) -> McpTool | None:
    """把远端 `mcp.types.Tool` 适配为 `McpTool`；非法名 / 重名 → None + 告警（F7/F8）。"""
    full_name = f"mcp__{server_name}__{t.name}"
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None
    if full_name in _known_names:
        print(f"[mcp] warn: skip duplicate tool {full_name}", file=sys.stderr)
        return None
    _known_names.add(full_name)
    description = t.description or f"来自 MCP server {server_name} 的工具 {t.name}"
    schema = dict(t.input_schema) if t.input_schema else {}
    if not schema:
        schema = {"type": "object"}  # 空 schema 兜底，避免 provider 拒收
    read_only = bool(t.annotations and t.annotations.read_only_hint)
    return McpTool(
        full_name=full_name,
        remote_name=t.name,
        description=description,
        parameters=schema,
        read_only=read_only,
        caller=session,
    )
