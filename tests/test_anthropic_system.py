"""anthropic 缓存通道守护回归：稳定系统块带 cache_control、环境块不带、reminder 并入末条 user。"""

import asyncio
from types import SimpleNamespace

from novacode.llm import Message, Request, System
from novacode.llm.anthropic_provider import AnthropicProvider


class FakeAnthropicUsage:
    def __init__(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeFinalMessage:
    stop_reason = "end_turn"
    usage = FakeAnthropicUsage()
    content = ()


class FakeStream:
    """模拟 anthropic MessageStream：一个非正文事件后即结束。"""

    def __init__(self) -> None:
        self._done = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._done:
            raise StopAsyncIteration
        self._done = True
        return SimpleNamespace(type="message_delta", delta=None)

    async def get_final_message(self):
        return FakeFinalMessage()


class FakeContext:
    """模拟 messages.stream 的 async context manager：捕获请求参数。"""

    def __init__(self) -> None:
        self.captured: dict | None = None

    async def __aenter__(self):
        return FakeStream()

    async def __aexit__(self, *exc):
        return False


def test_stable_block_cached_environment_not():
    """AC4/AC3：稳定块带 `cache_control: ephemeral` 断点、环境块不带；reminder 并入末条 user。"""
    ctx = FakeContext()

    class _Messages:
        def stream(self, **params):
            ctx.captured = params
            return ctx

    p = AnthropicProvider.__new__(AnthropicProvider)
    p._model = "fake-model"
    p._thinking = False
    p._client = SimpleNamespace(messages=_Messages())

    async def run() -> None:
        async for _ in p.stream(
            Request(
                messages=[Message(role="user", content="hi")],
                system=System(stable="稳定系统块", environment="环境信息块"),
                reminder="<system-reminder>计划提醒</system-reminder>",
            )
        ):
            pass

    asyncio.run(run())
    sys_blocks = ctx.captured["system"]
    assert sys_blocks[0]["text"] == "稳定系统块"
    assert sys_blocks[0]["cache_control"] == {"type": "ephemeral"}  # 稳定块打断点
    assert sys_blocks[1]["text"] == "环境信息块"
    assert "cache_control" not in sys_blocks[1]  # 环境块不带断点
    # reminder 并入末条 user 消息的 content 块（保角色交替 N3）
    last = ctx.captured["messages"][-1]
    assert last["role"] == "user"
    assert "<system-reminder>" in last["content"][-1]["text"]
