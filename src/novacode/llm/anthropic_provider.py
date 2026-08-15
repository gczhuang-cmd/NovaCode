"""anthropic 协议适配器：封装 `anthropic.AsyncAnthropic` 的流式接口。

支持缓存通道（F3）：稳定系统块打 `cache_control: ephemeral` 断点、环境块不打；
reminder 织入末条 user 消息（N3）；用量含缓存写/读字段（F4）。
思考增量（thinking_delta）接收即丢弃，不混入正文。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import anthropic

from ..config import ProviderConfig
from . import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
)


class AnthropicProvider:
    """anthropic 协议适配器。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)
        self._name = cfg.name
        self._model = cfg.model
        self._thinking = cfg.thinking

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict]:
        """协议无关 ToolDefinition → anthropic 线格式 tools。"""
        return [
            {
                "name": d.name,
                "description": d.description,
                "input_schema": d.input_schema,
            }
            for d in tools
        ]

    @staticmethod
    def _has_tool_history(msgs: list[Message]) -> bool:
        """历史中是否存在工具调用/结果回合（用于关闭 thinking）。"""
        return any(m.tool_calls or m.tool_results for m in msgs)

    def _to_anthropic_messages(self, msgs: list[Message]) -> list[dict]:
        """统一消息 → anthropic 线格式 messages。

        - assistant 带 tool_calls：content 为 text + tool_use 块数组；
        - ROLE_TOOL：把每个 ToolResult 拼成一条 user 消息的 tool_result 块数组。
        """
        out: list[dict] = []
        for m in msgs:
            if m.role == ROLE_ASSISTANT and m.tool_calls:
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for c in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": c.id,
                            "name": c.name,
                            "input": json.loads(c.input),
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
            elif m.role == ROLE_TOOL:
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.tool_call_id,
                                "content": r.content,
                                "is_error": r.is_error,
                            }
                            for r in m.tool_results
                        ],
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    @staticmethod
    def _append_reminder(messages: list[dict], reminder: str) -> None:
        """把 reminder 文本块并入末条消息的 content（保角色交替 N3）。

        末条缺失 / 末条 content 非 list / 末尾为 assistant 时，新起一条 user 消息。
        """
        block = {"type": "text", "text": reminder}
        if messages:
            content = messages[-1].get("content")
            if isinstance(content, list):
                content.append(block)
                return
        messages.append({"role": "user", "content": [block]})

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """向 anthropic 发起一轮流式对话，吐出统一 StreamEvent。

        稳定块打 `cache_control: ephemeral` 断点 → 缓存前缀 = 全部工具 + 稳定块；
        环境块与历史在断点后不缓存（env 每轮变化不影响前缀命中）。
        工具调用在流结束后由 `get_final_message()` 汇总上抛；用量含缓存写/读。
        """
        system: list[dict] = []
        if req.system.stable:
            system.append(
                {
                    "type": "text",
                    "text": req.system.stable,
                    "cache_control": {"type": "ephemeral"},  # 断点（默认 5m TTL）
                }
            )
        if req.system.environment:
            system.append({"type": "text", "text": req.system.environment})
        messages = self._to_anthropic_messages(req.messages)
        if req.reminder:
            self._append_reminder(messages, req.reminder)
        params: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system,
            "messages": messages,
        }
        if req.tools:
            params["tools"] = self._to_anthropic_tools(req.tools)
        # 历史含工具交互时关闭 thinking：Anthropic 要求回灌带原思考块（含 signature），
        # 本章按 spec 丢弃思考增量、不留签名，故对这类请求不启用 thinking 以避免 400。
        if self._thinking and not self._has_tool_history(req.messages):
            params["thinking"] = {"type": "enabled", "budget_tokens": 2048}
        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    # 正文增量：content_block_delta 且 delta 类型为 text_delta
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if getattr(delta, "type", "") == "text_delta":
                            yield StreamEvent(text=delta.text)
                        # 其它 delta（thinking_delta / signature_delta / input_json_delta）：接收即丢弃
                final = await stream.get_final_message()
            if final.stop_reason == "tool_use":
                calls = [
                    ToolCall(id=block.id, name=block.name, input=json.dumps(block.input))
                    for block in final.content
                    if getattr(block, "type", "") == "tool_use"
                ]
                if calls:
                    yield StreamEvent(tool_calls=calls)
            if final.usage is not None:
                usage = final.usage
                yield StreamEvent(
                    usage=Usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    )
                )
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except anthropic.BadRequestError as e:
            body = f"{e.message} {getattr(e, 'body', '')}"
            if "prompt is too long" in body or "context_length" in body:
                wrapped = PromptTooLongError("anthropic prompt too long")
                wrapped.__cause__ = e
                yield StreamEvent(err=wrapped)
            else:
                yield StreamEvent(err=e)
        except Exception as e:  # noqa: BLE001 鉴权失败 / 限流 / 网络中断 / 模型不存在等
            yield StreamEvent(err=e)
