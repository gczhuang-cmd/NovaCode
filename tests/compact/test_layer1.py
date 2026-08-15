"""第 1 层单测：单条 / 聚合 / 幂等 / 决策冻结 / 落盘失败降级。"""

from __future__ import annotations

from novacode.compact.layer1 import build_preview, offload_and_snip, spill_single
from novacode.compact.state import ContentReplacementState, SessionContext
from novacode.llm import Message, ToolResult


def _tool_msg(*contents: str) -> Message:
    return Message(
        role="tool",
        tool_results=[
            ToolResult(tool_call_id=f"t{i}", content=c, is_error=False)
            for i, c in enumerate(contents)
        ],
    )


def _ctx(tmp_path) -> SessionContext:
    return SessionContext(session_id="s", spill_dir=str(tmp_path))


def test_spill_single_idempotent(tmp_path):
    ctx = _ctx(tmp_path)
    spill_single(ctx, "id1", "hello")
    t1 = (tmp_path / "id1").stat().st_mtime_ns
    spill_single(ctx, "id1", "hello")
    assert (tmp_path / "id1").stat().st_mtime_ns == t1


def test_offload_single_result(tmp_path):
    ctx = _ctx(tmp_path)
    st = ContentReplacementState()
    big = "x" * 60000
    out = offload_and_snip([_tool_msg(big)], st, ctx)
    content = out[0].tool_results[0].content
    assert "original size: 60000 bytes" in content
    assert "[saved to]" in content
    assert "[head preview]" in content
    assert "不要凭头部预览猜测" in content
    assert (tmp_path / "t0").exists()
    # 头部预览（head preview 与提示行之间）≤ 2048 字节且 ≤ 20 行
    start = content.index("[head preview]") + len("[head preview]") + 1
    end = content.index("完整内容已保存")
    head_block = content[start:end].rstrip("\n")
    assert len(head_block.encode("utf-8")) <= 2048
    assert len(head_block.splitlines()) <= 20


def test_offload_decision_freeze(tmp_path):
    ctx = _ctx(tmp_path)
    st = ContentReplacementState()
    big = "x" * 60000
    out1 = offload_and_snip([_tool_msg(big)], st, ctx)
    out2 = offload_and_snip([_tool_msg(big)], st, ctx)
    assert out1[0].tool_results[0].content == out2[0].tool_results[0].content


def test_offload_aggregate(tmp_path):
    ctx = _ctx(tmp_path)
    st = ContentReplacementState()
    msg = _tool_msg("y" * 80000, "y" * 80000, "y" * 80000)  # 合计 240000 > 200000
    out = offload_and_snip([msg], st, ctx)
    results = out[0].tool_results
    replaced = sum(1 for r in results if "[content offloaded]" in r.content)
    assert replaced >= 2
    remaining = sum(len(r.content.encode("utf-8")) for r in results)
    assert remaining <= 200000


def test_offload_spill_failure_skip(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    st = ContentReplacementState()

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("novacode.compact.layer1.spill_single", _boom)
    big = "x" * 60000
    out = offload_and_snip([_tool_msg(big)], st, ctx)
    assert out[0].tool_results[0].content == big  # 保持原文
    assert st.result_for("t0", big) is None  # 未写账本 → 可重试


def test_preview_stable():
    p1 = build_preview(100, "head", "/spill/id")
    p2 = build_preview(100, "head", "/spill/id")
    assert p1 == p2
