"""WorktreeSession 数据结构 + JSON 原子持久化（ch14 T2，spec F3/F30）。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class WorktreeSession:
    """当前活跃的 Worktree 会话记录（spec F3）。"""

    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str
    hook_based: bool = False  # 预留

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> WorktreeSession:
        data = json.loads(raw)
        return cls(**data)


def load_session(path: Path) -> WorktreeSession | None:
    """读 session 文件；文件不存在/内容为空或 `null` 返回 None；JSON 非法抛异常。

    非法 JSON 由 Manager 构造层 catch 后警告清空（spec N5）。
    """
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw or raw == "null":
        return None
    return WorktreeSession.from_json(raw)


def save_session(path: Path, session: WorktreeSession | None) -> None:
    """原子写 session 文件（spec F30）：先写 tmp 再 os.replace；session=None 写 `null`。"""
    payload = session.to_json() if session is not None else "null"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def clear_session(path: Path) -> None:
    """清空 session（写 `null` 而非删文件，确保下次启动不误恢复，spec F31）。"""
    save_session(path, None)
