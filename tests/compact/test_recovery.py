"""恢复段单测：文件快照截断 / 5 条上限 / 工具集合一致 / 边界文案稳定。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from novacode.compact.recovery import (
    BOUNDARY_NOTICE,
    build_recovery_attachment,
    render_file_block,
)
from novacode.compact.state import FileReadRecord
from novacode.llm import ToolDefinition


def _rec(path: str, content: str, age_s: int) -> FileReadRecord:
    base = datetime.now(UTC)
    return FileReadRecord(path=path, content=content, timestamp=base + timedelta(seconds=age_s))


def test_render_file_block_truncate():
    out = render_file_block(_rec("a.py", "x" * 20000, 0))
    assert "(content truncated)" in out
    assert out.startswith("### a.py")


def test_build_recovery_attachment_limit_and_order():
    # snapshot 应已按时间戳倒序传入：f0 最新、f6 最旧（build_recovery_attachment 信任入参顺序）
    snap = [_rec(f"f{i}.py", "c", 7 - i) for i in range(7)]
    out = build_recovery_attachment(
        snap, [ToolDefinition(name="read_file", description="读", input_schema={})]
    )
    for i in (5, 6):
        assert f"f{i}.py" not in out
    assert out.index("f0.py") < out.index("f1.py")  # 最新在前


def test_build_recovery_attachment_tools_exact():
    defs = [ToolDefinition(name=f"t{i}", description=f"d{i}", input_schema={}) for i in range(3)]
    out = build_recovery_attachment([], defs)
    for d in defs:
        assert d.name in out
    for section in ("## 最近读过的文件", "## 当前可用工具", "## 边界提示"):
        assert section in out


def test_boundary_notice_stable():
    assert build_recovery_attachment([], []) == build_recovery_attachment([], [])
    assert "文件读取工具" in BOUNDARY_NOTICE
