"""compact 状态单测：决策冻结 / 熔断 / 会话目录 / 文件追踪。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    FileReadRecord,
    RecoveryState,
    new_session_context,
    open_session_context,
    parse_session_time,
)


def test_new_session_context_format_and_dir():
    with TemporaryDirectory() as d:
        s = new_session_context(d)
        # 新格式 YYYYMMDD-HHMMSS-<4hex>（ch09 F9），共 8+1+6+1+4=20 字符
        assert len(s.session_id) == 20
        assert s.session_id[8] == "-" and s.session_id[15] == "-"
        assert s.session_id[:8].isdigit() and s.session_id[9:15].isdigit()
        assert len(s.session_id[16:]) == 4
        assert all(c in "0123456789abcdef" for c in s.session_id[16:])
        assert s.session_dir == str(Path(d) / ".novacode" / "sessions" / s.session_id)
        assert s.spill_dir == str(Path(s.session_dir) / "tool-results")
        assert Path(s.spill_dir).is_dir()


def test_open_session_context_and_parse():
    with TemporaryDirectory() as d:
        s = new_session_context(d)
        opened = open_session_context(d, s.session_id)
        assert opened is not None
        assert opened.session_id == s.session_id
        assert opened.session_dir == s.session_dir
        # 目录不存在返回 None
        assert open_session_context(d, "20260101-000000-0000") is None
        # 新格式可解析时间戳（附本地时区 aware）
        t = parse_session_time(s.session_id)
        assert t is not None
        assert t.tzinfo is not None
        assert t.strftime("%Y%m%d-%H%M%S") == s.session_id[:15]
        # 旧格式解析失败返回 None
        assert parse_session_time("1717000000-abc12345") is None
        assert parse_session_time("not-a-session") is None


def test_new_session_context_distinct():
    with TemporaryDirectory() as d:
        assert new_session_context(d).session_id != new_session_context(d).session_id


def test_decide_once_freeze_kept():
    st = ContentReplacementState()
    st.decide_once("id1", "original", lambda: ("kept", ""))
    # 已被标记 kept：后续想 replaced 也无效，返回原文
    assert st.decide_once("id1", "original", lambda: ("replaced", "x")) == "original"


def test_decide_once_freeze_replaced():
    st = ContentReplacementState()
    st.decide_once("id1", "original", lambda: ("replaced", "preview"))
    # 已被标记 replaced：后续 kept 也返回 preview（预览冻结）
    assert st.decide_once("id1", "original", lambda: ("kept", "")) == "preview"


def test_decide_once_skip_does_not_mark():
    st = ContentReplacementState()
    st.decide_once("id1", "original", lambda: ("skip", ""))
    # skip 不写账本：下次仍可决策为 replaced
    assert st.decide_once("id1", "original", lambda: ("replaced", "preview")) == "preview"


def test_result_for_unknown_none():
    assert ContentReplacementState().result_for("id1", "orig") is None


def test_auto_tracking_consecutive():
    cb = CompactCircuitBreaker()
    cb.record_failure()
    cb.record_failure()
    assert cb.tripped() is False
    cb.record_success()  # 成功清零
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.tripped() is True


def test_recovery_snapshot_order():
    rs = RecoveryState()
    t0 = datetime.now(UTC)
    rs._files["a"] = FileReadRecord("a", "1", t0)
    rs._files["b"] = FileReadRecord("b", "2", t0 + timedelta(seconds=1))
    snap = rs.snapshot()
    assert [r.path for r in snap] == ["b", "a"]


def test_recovery_snapshot_is_copy():
    rs = RecoveryState()
    rs.record_file(str(Path("x").resolve()), "c")
    snap = rs.snapshot()
    snap.clear()
    assert len(rs.snapshot()) == 1
