"""openai 协议适配器：封装 `openai.AsyncOpenAI` 的流式接口。

`cfg.thinking` 对该协议忽略（OpenAI reasoning 不经 chat.completions 正文返回）。
系统提示单条 system 消息（stable 居前缀 + environment 拼接），靠端点前缀缓存自动受益（尽力而为）；
reminder 追加尾部 user 消息；用量含 `cached_tokens`（缓存读取，端点不返回则为 0）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import openai

from ..config import ProviderConfig

logger = logging.getLogger(__name__)
from . import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    ToolDelta,
    Usage,
)


class OpenAIProvider:
    """openai 协议适配器。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._client = openai.AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None)
        self._name = cfg.name
        self._model = cfg.model

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict]:
        """协议无关 ToolDefinition → openai 线格式 tools。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": d.description,
                    "parameters": d.input_schema,
                },
            }
            for d in tools
        ]

    @staticmethod
    def _system_content(req: Request) -> str:
        """系统提示：stable 居前缀，environment 非空时拼接在其后（单条 system 消息）。"""
        parts = [p for p in (req.system.stable, req.system.environment) if p]
        return "\n\n".join(parts)

    def _to_openai_messages(self, req: Request) -> list[dict]:
        """Request → openai 线格式 messages。

        - 首条 system = stable + environment（单条，兼容端点对多条 system 支持不一）；
        - assistant 带 tool_calls：发 assistant + tool_calls 数组；
        - ROLE_TOOL：每个 ToolResult 发一条 tool 角色消息；
        - reminder 追加尾部 user 消息（OpenAI 容忍连续 user / tool 后接 user）。
        """
        out: list[dict] = []
        system_content = self._system_content(req)
        if system_content:
            out.append({"role": "system", "content": system_content})
        for m in req.messages:
            if m.role == ROLE_ASSISTANT and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": c.input or "{}"},
                            }
                            for c in m.tool_calls
                        ],
                    }
                )
            elif m.role == ROLE_TOOL:
                for r in m.tool_results:
                    out.append(
                        {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                    )
            else:
                out.append({"role": m.role, "content": m.content})
        if req.reminder:
            out.append({"role": "user", "content": req.reminder})
        return out

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """向 openai 发起一轮流式对话，吐出统一 StreamEvent。

        流式期间按 `delta.tool_calls[i].index` 拼接参数碎片；流结束后整体上抛 ToolCall；
        末尾 usage chunk 透传本轮用量（含 `cached_tokens` 缓存读取）。
        """
        create_kwargs: dict = {
            "model": self._model,
            "messages": self._to_openai_messages(req),
            "stream": True,
            "stream_options": {"include_usage": True},  # 不开启则流式末尾 usage 块为空
        }
        if req.tools:
            create_kwargs["tools"] = self._to_openai_tools(req.tools)
        tool_calls_buf: dict[int, dict[str, str]] = {}
        try:
            stream = await self._client.chat.completions.create(**create_kwargs)
            async for chunk in stream:
                # usage 可能在流末尾的任一 chunk 出现——无论 choices 是否为空都要检查。
                # （OpenAI 官方 usage chunk 的 choices 为空；DeepSeek 的 usage chunk 带 choices=1。）
                if chunk.usage is not None:
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    cached = getattr(details, "cached_tokens", 0) or 0
                    details2 = getattr(chunk.usage, "completion_tokens_details", None)
                    reasoning = getattr(details2, "reasoning_tokens", 0) or 0
                    yield StreamEvent(
                        usage=Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_write=0,  # OpenAI 前缀缓存无显式写计数
                            cache_read=cached,
                            reasoning_tokens=reasoning,
                        )
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    yield StreamEvent(text=text)
                # DeepSeek 推理模型思考增量：reasoning_content 是 openai 兼容协议的非标准字段，
                # 仅展示用（StreamEvent.thinking），不混入正文。
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield StreamEvent(thinking=reasoning)
                for tc in getattr(delta, "tool_calls", None) or []:
                    buf = tool_calls_buf.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        buf["id"] = tc.id
                    name = tc.function.name if (tc.function and tc.function.name) else ""
                    if name:
                        buf["name"] = name
                    args = tc.function.arguments if (tc.function and tc.function.arguments) else ""
                    if args:
                        buf["args"] = buf.get("args", "") + args
                    # 工具参数增量：仅展示用（字符进度）。name 或 args 任一非空即转发——
                    # 首个带 name 的增量（args 常为空）也必须转发，否则进度区丢失工具名。
                    if name or args:
                        yield StreamEvent(
                            tool_delta=[ToolDelta(index=tc.index, name=name, args=args)]
                        )
            if tool_calls_buf:
                calls = [
                    ToolCall(
                        id=buf["id"],
                        name=buf["name"],
                        input=buf.get("args") or "{}",  # 空 arguments 归一为 {}，避免严格端点 400
                    )
                    for _, buf in sorted(tool_calls_buf.items())
                ]
                yield StreamEvent(tool_calls=calls)
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except openai.BadRequestError as e:
            logger.warning("openai 4xx: %s body=%s", e, getattr(e, "body", None))
            err_obj = getattr(e, "error", None)
            code = (
                err_obj.get("code") if isinstance(err_obj, dict) else getattr(err_obj, "code", None)
            )
            if code == "context_length_exceeded":
                wrapped = PromptTooLongError("openai context length exceeded")
                wrapped.__cause__ = e
                yield StreamEvent(err=wrapped)
            else:
                yield StreamEvent(err=e)
        except Exception as e:  # noqa: BLE001 鉴权失败 / 限流 / 网络中断 / 模型不存在等
            yield StreamEvent(err=e)
