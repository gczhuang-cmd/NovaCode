"""自动笔记子包：笔记存储、索引管理、异步 LLM 更新（ch09 第 4 层）。"""

from .manager import Manager
from .prompts import MEMORY_UPDATE_SYSTEM_PROMPT
from .store import Store
from .types import Note, NoteType, UpdateAction

__all__ = [
    "MEMORY_UPDATE_SYSTEM_PROMPT",
    "Manager",
    "Note",
    "NoteType",
    "Store",
    "UpdateAction",
]
