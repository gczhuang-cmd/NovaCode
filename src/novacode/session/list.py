"""会话列表扫描：SessionInfo 构建与按修改时间倒序排列（ch09 F18~F20）。

只返回「目录名能解析出新格式时间戳 + 包含 conversation.jsonl」的有效会话；
旧格式 session ID 不展示（N3）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..compact.state import parse_session_time

_TITLE_MAX = 50  # 标题截断长度（含省略号，F20）


@dataclass
class SessionInfo:
    """会话列表中一项的摘要信息。"""

    id: str  # session ID（目录名）
    title: str  # 第一条 user 消息内容（截断）
    modified_at: datetime  # 最后修改时间（aware）
    model: str  # 模型标签
    size: int  # JSONL 文件大小（字节）
    dir: str  # 会话目录绝对路径


def _relative_time(dt: datetime, now: datetime | None = None) -> str:
    """相对时间描述：`just now` / `N minutes ago` / `N hours ago` / `N days ago`。"""
    now = now or datetime.now().astimezone()
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def _first_user_meta(jsonl: Path) -> tuple[str, str]:
    """读第一条 role=user 消息 → (标题截断 50 字符含省略号, model)。坏行/标记行跳过。"""
    title = ""
    model = ""
    try:
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "compact":
                    continue
                if data.get("role") == "user":
                    title = str(data.get("content") or "")
                    model = str(data.get("model") or "")
                    break
    except OSError:
        pass
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 3] + "..."
    return title, model


def list_sessions(sessions_dir: str) -> list[SessionInfo]:
    """扫描 sessions_dir 下的有效会话，按最后修改时间倒序返回（F18/F20）。"""
    root = Path(sessions_dir)
    if not root.is_dir():
        return []
    items: list[SessionInfo] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        if parse_session_time(d.name) is None:
            continue  # 旧格式 ID 不展示（N3）
        jsonl = d / "conversation.jsonl"
        if not jsonl.is_file():
            continue
        try:
            stat = jsonl.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).astimezone()
        except OSError:
            continue
        if size == 0:
            continue  # 空会话（无任何消息）不展示
        title, model = _first_user_meta(jsonl)
        items.append(
            SessionInfo(
                id=d.name,
                title=title,
                modified_at=mtime,
                model=model,
                size=size,
                dir=str(d),
            )
        )
    items.sort(key=lambda s: s.modified_at, reverse=True)
    return items
