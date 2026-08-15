"""会话加载恢复：从 conversation.jsonl 恢复消息列表（ch09 F21）。

从最后一个 compact 标记之后加载，跳过坏行，截断孤立工具调用；返回
`(messages, last_ts)`，`last_ts` 供恢复流程的时间跨度检查。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..llm import Message, ToolCall, ToolResult


def _entry_to_msg(data: dict) -> Message:
    """把 JSONL 行 dict 还原为 llm.Message（tool_calls/tool_results 结构还原）。"""
    msg = Message(role=str(data.get("role") or "user"), content=str(data.get("content") or ""))
    calls = data.get("tool_calls") or []
    results = data.get("tool_results") or []
    if calls:
        msg.tool_calls = [
            ToolCall(
                id=str(c.get("id") or ""),
                name=str(c.get("name") or ""),
                input=str(c.get("input") or ""),
            )
            for c in calls
            if isinstance(c, dict)
        ]
    if results:
        msg.tool_results = [
            ToolResult(
                tool_call_id=str(r.get("tool_call_id") or ""),
                content=str(r.get("content") or ""),
                is_error=bool(r.get("is_error", False)),
            )
            for r in results
            if isinstance(r, dict)
        ]
    return msg


def _truncate_orphaned_tool_calls(msgs: list[Message]) -> list[Message]:
    """末尾是带 tool_calls 的 assistant 且无后续 tool 消息 → 截断到该条之前（F21）。"""
    if not msgs:
        return msgs
    last = msgs[-1]
    if last.role == "assistant" and last.tool_calls:
        return list(msgs[:-1])
    return msgs


def load_session(session_dir: str) -> tuple[list[Message], int | None]:
    """从 conversation.jsonl 恢复消息列表。

    逐行读取、JSON 解析失败的行跳过（坏行容错）；从最后一个 `compact` 标记之后
    开始构建消息；截断孤立工具调用。返回 `(messages, last_ts)`：`last_ts` 为该段
    最后一条消息的 ts（时间跨度检查用，无消息返回 None）。
    """
    jsonl = Path(session_dir) / "conversation.jsonl"
    if not jsonl.is_file():
        return [], None
    lines: list[tuple[int, dict]] = []
    last_compact_index = -1
    try:
        with open(jsonl, encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行跳过（F21）
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "compact":
                    last_compact_index = idx
                    continue
                lines.append((idx, data))
    except OSError:
        return [], None
    selected = [(idx, d) for idx, d in lines if idx > last_compact_index]
    msgs = [_entry_to_msg(d) for _, d in selected]
    msgs = _truncate_orphaned_tool_calls(msgs)
    last_ts = selected[-1][1].get("ts") if selected else None
    if not isinstance(last_ts, int):
        last_ts = None
    return msgs, last_ts
