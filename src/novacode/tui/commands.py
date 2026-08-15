"""压缩状态文案（ch08 F24a/F24b 的统一提示）。

ch10：命令注册表、CommandContext 与各 handler 已迁入 `novacode.command` 包；
本文件仅保留 `format_compact_notice`，供 `_stream_reply` 在收到 compact 事件时打印。
"""

from __future__ import annotations

from ..agent import CompactEvent, CompactPhase


def format_compact_notice(ev: CompactEvent) -> str:
    """统一压缩状态文案（自动 / 紧急 / 手动三条路径共用，F24a/F24b）。"""
    if ev.phase == CompactPhase.BEFORE_AUTO:
        return "正在压缩上下文..."
    if ev.phase == CompactPhase.BEFORE_EMERGENCY:
        return "上下文撞墙，自动压缩中..."
    if ev.err is not None:
        return f"压缩失败：{ev.err}"
    return f"已压缩，token 从 {ev.before} 降至 {ev.after}"
