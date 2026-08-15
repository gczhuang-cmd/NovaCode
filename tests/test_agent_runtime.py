"""SessionRuntime 单测：reset_for_new_session 重置会话级状态（ch10 T0c，ch12 增 pending_reminders）。"""

from __future__ import annotations

import tempfile

from novacode.agent.runtime import SessionRuntime
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)


def _runtime(tmp_path):
    ctx = new_session_context(str(tmp_path))
    r = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=ctx,
        context_window=123456,
    )
    return ctx, r


def test_reset_for_new_session_replaces_session_and_zeroes(tmp_path):
    """调用后 compact 三子状态回到空、usage/turn 清零、session 被替换、context_window 保留。"""
    old_ctx, r = _runtime(tmp_path)
    r.usage_anchor = 999
    r.anchor_msg_len = 42
    r.turn_count = 5
    r.recovery.record_file("a", "b")
    r.auto_tracking.record_failure()

    new_ctx = new_session_context(str(tmp_path))
    assert new_ctx.session_id != old_ctx.session_id
    r.reset_for_new_session(new_ctx)

    assert r.session.session_id == new_ctx.session_id
    assert r.session.session_dir == new_ctx.session_dir
    assert r.usage_anchor == 0
    assert r.anchor_msg_len == 0
    assert r.turn_count == 0
    # compact 三子状态为新实例（空账本 / 空文件追踪 / 未熔断）
    assert r.replacement.result_for("x", "orig") is None
    assert r.recovery.snapshot() == []
    assert not r.auto_tracking.tripped()
    assert r.context_window == 123456  # 与 provider 相关，保留
    # ch12：hook 注入的 reminder 队列随新会话清空
    assert r.pending_reminders == []


def test_pending_reminders_append_take():
    """append_reminders 追加、take_reminders 取出并清空（ch12 F33）。"""
    _, r = _runtime(tempfile.gettempdir())
    r.append_reminders(["a", "b"])
    r.append_reminders(["c"])
    assert r.pending_reminders == ["a", "b", "c"]
    assert r.take_reminders() == ["a", "b", "c"]
    assert r.pending_reminders == []
    assert r.take_reminders() == []


def test_reset_for_new_session_clears_pending_reminders(tmp_path):
    """reset_for_new_session 清空未消费的 hook reminder（ch12 F21）。"""
    _, r = _runtime(tmp_path)
    r.append_reminders(["zh-cn"])
    new_ctx = new_session_context(str(tmp_path))
    r.reset_for_new_session(new_ctx)
    assert r.pending_reminders == []
