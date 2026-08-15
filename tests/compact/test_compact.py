"""manage_context 编排单测：自动 / 手动 / 紧急三路径。"""

from __future__ import annotations

import pytest

from novacode.compact import TriggerKind, manage_context
from novacode.compact.compact import ManageInput
from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
)
from novacode.conversation import Conversation
from novacode.llm import StreamEvent

SUMMARY_OK = [
    StreamEvent(text="<summary>## 1 主要请求和意图\nx</summary>"),
    StreamEvent(done=True),
]


class FakeProvider:
    """脚本化 provider：摘要请求（tools 为空）计数。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = responses
        self.call_count = 0
        self.summarize_calls = 0

    async def stream(self, req):
        if not req.tools:
            self.summarize_calls += 1
        idx = min(self.call_count, len(self.responses) - 1) if self.responses else 0
        self.call_count += 1
        if not self.responses:
            return
        for ev in self.responses[idx]:
            yield ev


def _make_input(
    tmp_path,
    conv,
    prov,
    trigger,
    estimated_token: int = 100,
    context_window: int = 200000,
) -> ManageInput:
    return ManageInput(
        conv=conv,
        provider=prov,
        model="m",
        context_window=context_window,
        tool_defs=[],
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=SessionContext("s", str(tmp_path)),
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=estimated_token,
        trigger=trigger,
    )


async def test_manage_context_manual_bypasses_threshold(tmp_path):
    conv = Conversation()
    conv.add_user("hi")
    prov = FakeProvider([SUMMARY_OK])
    in_ = _make_input(tmp_path, conv, prov, TriggerKind.MANUAL, estimated_token=500)
    out = await manage_context(in_)
    assert prov.summarize_calls == 1
    assert out.before_tokens == 500
    assert conv.messages()[0].role == "user"


async def test_manage_context_auto_skipped_below_threshold(tmp_path):
    conv = Conversation()
    conv.add_user("hi")
    prov = FakeProvider([])
    in_ = _make_input(tmp_path, conv, prov, TriggerKind.AUTO, estimated_token=100)
    await manage_context(in_)
    assert prov.summarize_calls == 0


async def test_manage_context_auto_triggers_on_threshold(tmp_path):
    conv = Conversation()
    conv.add_user("x" * 100000)  # ~28k token，跨越 50000-33000=17000 阈值
    prov = FakeProvider([SUMMARY_OK])
    in_ = _make_input(
        tmp_path, conv, prov, TriggerKind.AUTO, estimated_token=30000, context_window=50000
    )
    await manage_context(in_)
    assert prov.summarize_calls == 1
    assert conv.messages()[0].role == "user"  # 摘要+恢复合并为一条 user


async def test_manage_context_auto_skipped_when_tripped(tmp_path):
    conv = Conversation()
    conv.add_user("x" * 100000)
    prov = FakeProvider([])
    in_ = _make_input(
        tmp_path, conv, prov, TriggerKind.AUTO, estimated_token=30000, context_window=50000
    )
    in_.auto_tracking.record_failure()
    in_.auto_tracking.record_failure()
    in_.auto_tracking.record_failure()  # 熔断
    await manage_context(in_)
    assert prov.summarize_calls == 0


async def test_manage_context_auto_failure_raises(tmp_path):
    conv = Conversation()
    conv.add_user("x" * 100000)
    prov = FakeProvider([[StreamEvent(err=RuntimeError("boom"))]])
    in_ = _make_input(
        tmp_path, conv, prov, TriggerKind.AUTO, estimated_token=30000, context_window=50000
    )
    with pytest.raises(RuntimeError):
        await manage_context(in_)
    assert in_.auto_tracking.tripped() is False  # 1 次失败未达熔断阈值


async def test_manage_context_emergency_runs_layer1_then_force(tmp_path):
    conv = Conversation()
    conv.add_user("hi")
    prov = FakeProvider([SUMMARY_OK])
    in_ = _make_input(tmp_path, conv, prov, TriggerKind.EMERGENCY, estimated_token=200000)
    await manage_context(in_)
    assert prov.summarize_calls == 1
