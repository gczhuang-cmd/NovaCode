"""第 1 层预防性压缩：单条 / 聚合落盘 + 预览替换 + 决策冻结（F1~F6）。

纯函数式：输入消息列表，输出替换后的新列表，不修改入参；落盘失败降级为不替换。
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from ..llm import Message
from .const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)
from .state import ContentReplacementState, SessionContext

logger = logging.getLogger(__name__)


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    """把单条 tool_result 内容写入 spill_dir/<tool_use_id>（F3）。

    幂等：文件已存在则不重写、不报错；失败抛 OSError 由上层捕获。
    """
    path = Path(session.spill_dir) / tool_use_id
    if path.exists():
        return
    path.write_bytes(content.encode("utf-8"))


def _head_preview(content: str) -> str:
    """取预览头部：先按行截到 PREVIEW_HEAD_LINES，再按字节截到 PREVIEW_HEAD_BYTES（F4）。"""
    lines = content.splitlines(keepends=True)[:PREVIEW_HEAD_LINES]
    head = "".join(lines)
    if len(head.encode("utf-8")) > PREVIEW_HEAD_BYTES:
        # 字节级截断（UTF-8 边界对齐：截到不产生半个字符的最近边界）
        head_bytes = head.encode("utf-8")[:PREVIEW_HEAD_BYTES]
        head = head_bytes.decode("utf-8", errors="ignore")
    return head


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    """构造替换体字符串：原始字节数 + 头部预览 + 落盘路径 + 重读提示（F4）。

    只在首次决策为替换的瞬间调用一次；之后各轮复用账本中存好的字符串。
    """
    lines = [
        f"[content offloaded] original size: {original_bytes} bytes",
        f"[saved to] {spill_path}",
        "[head preview]",
    ]
    if head:
        lines.append(head)
    lines.append(
        "完整内容已保存到上述路径，如需查看请用文件读取工具读取该路径，不要凭头部预览猜测全文"
    )
    return "\n".join(lines)


def _spill_decide(session: SessionContext, tool_use_id: str, content: str) -> tuple[str, str]:
    """decide 回调：落盘 → 构造预览；落盘失败返回 ("skip", "")（N6 降级，不写账本）。"""
    try:
        spill_single(session, tool_use_id, content)
    except OSError:
        return ("skip", "")
    spill_path = str(Path(session.spill_dir) / tool_use_id)
    return (
        "replaced",
        build_preview(len(content.encode("utf-8")), _head_preview(content), spill_path),
    )


def offload_and_snip(
    msgs: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[Message]:
    """遍历 tool 消息的 tool_results，超阈值落盘 + 替换 + 决策冻结（F1/F2/F2a/F5）。

    流程（每条 tool 消息独立）：
      1. 已决策项（`state.result_for`）直接取账本存量结果，不重新决策；
      2. 未决策项进候选列表，按字节倒序处理：
         a. 单条 > SINGLE_RESULT_LIMIT：落盘替换（replaced）；
         b. 剩余聚合 > MESSAGE_AGGREGATE_LIMIT：继续按倒序落盘下一项，直到聚合回落；
         c. 未落盘的项 kept；
      3. 落盘失败 → skip（不写账本，下次重试）。
    返回深拷贝后的新列表。
    """
    out = copy.deepcopy(msgs)
    for msg in out:
        if msg.role != "tool":
            continue
        results = msg.tool_results
        # 已决策项直接改写 content（reused preview / kept 原文）
        for r in results:
            stored = state.result_for(r.tool_call_id, r.content)
            if stored is not None:
                r.content = stored
        # 未决策项 → 候选列表
        candidates = [r for r in results if state.result_for(r.tool_call_id, r.content) is None]
        candidates.sort(key=lambda r: len(r.content.encode("utf-8")), reverse=True)
        remaining = sum(len(r.content.encode("utf-8")) for r in candidates)
        for r in candidates:
            size = len(r.content.encode("utf-8"))
            over_single = size > SINGLE_RESULT_LIMIT
            over_aggregate = remaining > MESSAGE_AGGREGATE_LIMIT
            if over_single or over_aggregate:
                new_content = state.decide_once(
                    r.tool_call_id,
                    r.content,
                    lambda id_=r.tool_call_id, c=r.content: _spill_decide(session, id_, c),
                )
                r.content = new_content
                remaining -= size  # 原字节移出聚合预算（content 已换成预览体）
            else:
                state.decide_once(r.tool_call_id, r.content, lambda: ("kept", ""))
    return out
