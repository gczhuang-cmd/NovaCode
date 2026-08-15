"""compact 包测试共享 fixture：脚本化 fake provider。"""

from __future__ import annotations

import pytest

from novacode.llm import StreamEvent


class FakeProvider:
    """脚本化 provider：摘要请求（tools 为空）计数；按调用次数依次吐 responses。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = responses
        self.call_count = 0
        self.summarize_calls = 0  # 摘要请求（tools 为空）计数
        self.normal_calls = 0  # 主对话请求计数

    async def stream(self, req):
        if not req.tools:
            self.summarize_calls += 1
        else:
            self.normal_calls += 1
        idx = min(self.call_count, len(self.responses) - 1) if self.responses else 0
        self.call_count += 1
        if not self.responses:
            return
        for ev in self.responses[idx]:
            yield ev


@pytest.fixture
def fake_provider():
    """返回一个默认无响应的 provider，测试按需配置。"""
    return FakeProvider([])
