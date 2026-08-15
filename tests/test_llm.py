"""llm 适配器单测：openai/anthropic 的 usage 解析（含「usage chunk 带 choices」的 DeepSeek 形态）。"""

import asyncio
from types import SimpleNamespace

from novacode.llm import Message, Request, ToolCall, ToolDelta, Usage
from novacode.llm.anthropic_provider import AnthropicProvider
from novacode.llm.openai_provider import OpenAIProvider


class FakeDelta:
    def __init__(self, content=None, tool_calls=None, reasoning_content=None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


class FakeChoice:
    def __init__(self, delta) -> None:
        self.delta = delta


class FakeUsage:
    def __init__(
        self, prompt_tokens: int, completion_tokens: int, completion_tokens_details=None
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.completion_tokens_details = completion_tokens_details


class FakeChunk:
    def __init__(self, choices, usage=None) -> None:
        self.choices = choices
        self.usage = usage


class FakeCompletions:
    """fake `chat.completions`：create 返回按序 yield chunk 的 async generator。"""

    def __init__(self, chunks: list[FakeChunk]) -> None:
        self._chunks = chunks

    async def create(self, **kwargs):
        async def _gen():
            for c in self._chunks:
                yield c

        return _gen()


def test_openai_usage_in_choices_chunk():
    """DeepSeek 形态：usage 出现在 choices 非空的末尾 chunk，不能被漏掉。"""
    chunks = [
        FakeChunk([FakeChoice(FakeDelta(content="你"))]),
        FakeChunk([FakeChoice(FakeDelta(content="好"))]),
        # 关键：usage 与 choices=1 同 chunk（OpenAI 官方是 choices 为空）
        FakeChunk([FakeChoice(FakeDelta(content=None))], usage=FakeUsage(238, 43)),
    ]
    p = OpenAIProvider.__new__(OpenAIProvider)
    p._model = "fake-model"
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(chunks)))

    text: list[str] = []
    usage: Usage | None = None

    async def run() -> None:
        nonlocal usage
        async for ev in p.stream(Request(messages=[Message(role="user", content="hi")])):
            if ev.usage is not None:
                usage = ev.usage
            if ev.text:
                text.append(ev.text)
            if ev.done:
                break

    asyncio.run(run())
    assert "".join(text) == "你好"
    assert usage == Usage(input_tokens=238, output_tokens=43)


def test_openai_thinking_delta():
    """DeepSeek 形态：reasoning_content 增量被转发为 StreamEvent.thinking，不混入正文。"""
    chunks = [
        FakeChunk([FakeChoice(FakeDelta(content=None, reasoning_content="先算"))]),
        FakeChunk([FakeChoice(FakeDelta(content=None, reasoning_content="乘法"))]),
        FakeChunk([FakeChoice(FakeDelta(content="答案是 11"))]),
        FakeChunk([FakeChoice(FakeDelta(content=None))], usage=FakeUsage(117, 698)),
    ]
    p = OpenAIProvider.__new__(OpenAIProvider)
    p._model = "fake-model"
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(chunks)))

    text: list[str] = []
    thinking: list[str] = []

    async def run() -> None:
        async for ev in p.stream(Request(messages=[Message(role="user", content="hi")])):
            if ev.thinking:
                thinking.append(ev.thinking)
            if ev.text:
                text.append(ev.text)
            if ev.done:
                break

    asyncio.run(run())
    assert "".join(thinking) == "先算乘法"
    assert "".join(text) == "答案是 11"


def test_openai_tool_delta_and_reasoning_usage():
    """工具参数分片 → tool_delta 逐段转发（增量），流末仍产出完整 tool_calls；reasoning_tokens 透传。"""
    chunks = [
        FakeChunk(
            [
                FakeChoice(
                    FakeDelta(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_1",
                                function=SimpleNamespace(name="write_file", arguments='{"path":'),
                            )
                        ],
                    )
                )
            ]
        ),
        FakeChunk(
            [
                FakeChoice(
                    FakeDelta(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="",
                                function=SimpleNamespace(name="", arguments='"D:\\\\x"}'),
                            )
                        ],
                    )
                )
            ]
        ),
        FakeChunk(
            [FakeChoice(FakeDelta(content=None))],
            usage=FakeUsage(
                100, 50, completion_tokens_details=SimpleNamespace(reasoning_tokens=30)
            ),
        ),
    ]
    p = OpenAIProvider.__new__(OpenAIProvider)
    p._model = "fake-model"
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(chunks)))

    deltas: list[ToolDelta] = []
    calls: list[ToolCall] = []
    usage: Usage | None = None

    async def run() -> None:
        nonlocal usage
        async for ev in p.stream(Request(messages=[Message(role="user", content="hi")])):
            if ev.tool_delta:
                deltas.extend(ev.tool_delta)
            if ev.tool_calls:
                calls.extend(ev.tool_calls)
            if ev.usage is not None:
                usage = ev.usage
            if ev.done:
                break

    asyncio.run(run())
    assert deltas == [
        ToolDelta(index=0, name="write_file", args='{"path":'),
        ToolDelta(index=0, name="", args='"D:\\\\x"}'),
    ]
    assert calls == [ToolCall(id="call_1", name="write_file", input='{"path":"D:\\\\x"}')]
    assert usage == Usage(input_tokens=100, output_tokens=50, reasoning_tokens=30)


def test_openai_tool_delta_name_only_first_delta():
    """首个增量带 name 但 args 为空时也要转发（进度区保留工具名，F4）。"""
    chunks = [
        FakeChunk(
            [
                FakeChoice(
                    FakeDelta(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_9",
                                function=SimpleNamespace(name="write_file", arguments=None),
                            )
                        ],
                    )
                )
            ]
        ),
        FakeChunk(
            [
                FakeChoice(
                    FakeDelta(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="",
                                function=SimpleNamespace(name="", arguments='{"path":"x"}'),
                            )
                        ],
                    )
                )
            ]
        ),
        FakeChunk([FakeChoice(FakeDelta(content=None))], usage=FakeUsage(10, 20)),
    ]
    p = OpenAIProvider.__new__(OpenAIProvider)
    p._model = "fake-model"
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(chunks)))

    deltas: list[ToolDelta] = []

    async def run() -> None:
        async for ev in p.stream(Request(messages=[Message(role="user", content="hi")])):
            if ev.tool_delta:
                deltas.extend(ev.tool_delta)
            if ev.done:
                break

    asyncio.run(run())
    # 首段带 name、args 空；第二段 args 增量
    assert deltas[0] == ToolDelta(index=0, name="write_file", args="")
    assert deltas[1] == ToolDelta(index=0, name="", args='{"path":"x"}')


def test_openai_usage_in_empty_choices_chunk():
    """OpenAI 官方形态：usage 在 choices 为空的末尾 chunk，同样被解析。"""
    chunks = [
        FakeChunk([FakeChoice(FakeDelta(content="好"))]),
        FakeChunk([], usage=FakeUsage(100, 20)),
    ]
    p = OpenAIProvider.__new__(OpenAIProvider)
    p._model = "fake-model"
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(chunks)))

    usage: Usage | None = None

    async def run() -> None:
        nonlocal usage
        async for ev in p.stream(Request(messages=[Message(role="user", content="hi")])):
            if ev.usage is not None:
                usage = ev.usage
            if ev.done:
                break

    asyncio.run(run())
    assert usage == Usage(input_tokens=100, output_tokens=20)


# ─── anthropic：用量走独立的 final_message.usage 路径（不依赖流式 chunk 结构）───


class FakeAnthropicUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeFinalMessage:
    def __init__(self, stop_reason: str, usage, content=()) -> None:
        self.stop_reason = stop_reason
        self.usage = usage
        self.content = content


class FakeAnthropicEvent:
    def __init__(self, type: str, delta=None) -> None:
        self.type = type
        self.delta = delta


class FakeAnthropicDelta:
    def __init__(self, type: str, text: str | None = None) -> None:
        self.type = type
        self.text = text


class FakeAnthropicStream:
    """模拟 anthropic MessageStream：可 async 迭代 + get_final_message。"""

    def __init__(self, events, final) -> None:
        self._events = list(events)
        self._final = final

    def __aiter__(self):
        self._it = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_message(self):
        return self._final


class FakeAnthropicContext:
    def __init__(self, events, final) -> None:
        self._stream = FakeAnthropicStream(events, final)

    async def __aenter__(self):
        return self._stream

    async def __aexit__(self, *exc):
        return False


def test_anthropic_usage_after_stream():
    """anthropic：用量从流结束后 final_message.usage 读取，不依赖 chunk 结构。"""
    final = FakeFinalMessage(stop_reason="end_turn", usage=FakeAnthropicUsage(500, 88), content=())
    events = [FakeAnthropicEvent("content_block_delta", FakeAnthropicDelta("text_delta", "你好"))]

    class _Messages:
        def stream(self, **params):
            return FakeAnthropicContext(events, final)

    p = AnthropicProvider.__new__(AnthropicProvider)
    p._model = "fake-model"
    p._thinking = False
    p._client = SimpleNamespace(messages=_Messages())

    text: list[str] = []
    usage: Usage | None = None

    async def run() -> None:
        nonlocal usage
        async for ev in p.stream(Request(messages=[Message(role="user", content="hi")])):
            if ev.usage is not None:
                usage = ev.usage
            if ev.text:
                text.append(ev.text)
            if ev.done:
                break

    asyncio.run(run())
    assert "".join(text) == "你好"
    assert usage == Usage(input_tokens=500, output_tokens=88)
