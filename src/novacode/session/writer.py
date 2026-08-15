"""会话 JSONL 写入器：Entry 序列化 + 追加写 + compact 标记（ch09 F11~F16）。

Writer 持有打开的文件句柄与一把线程锁，保证多协程/线程追加原子性；每次 append
后 flush + fsync 刷盘（F15）。崩溃最多丢最后一行不完整的写入（F14）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self

from ..llm import Message


@dataclass
class Entry:
    """JSONL 中一行的 dataclass 表示（ch09 F11）。"""

    role: str = ""  # "user" / "assistant" / "tool"
    content: str = ""
    tool_calls: list | None = None  # 仅 assistant（序列化为 list[dict]）
    tool_results: list | None = None  # 仅 tool
    ts: int = 0  # 写入时刻的 Unix 时间戳（秒）
    model: str | None = None  # 仅首条消息携带
    type: str | None = None  # "compact" 或省略


def _msg_to_entry(msg: Message, model: str | None, is_first: bool) -> Entry:
    """把 llm.Message 序列化为 Entry（tool_calls/tool_results 转 list[dict]）。"""
    entry = Entry(role=msg.role, content=msg.content, ts=int(time.time()))
    if msg.tool_calls:
        entry.tool_calls = [asdict(tc) for tc in msg.tool_calls]
    if msg.tool_results:
        entry.tool_results = [asdict(tr) for tr in msg.tool_results]
    if is_first:
        entry.model = model
    return entry


class Writer:
    """向 conversation.jsonl 追加写入（线程安全，F15）。"""

    def __init__(self, session_dir: str) -> None:
        os.makedirs(session_dir, exist_ok=True)
        self._jsonl = str(Path(session_dir) / "conversation.jsonl")
        self.path = self._jsonl  # 会话存档文件绝对路径（ch10 /session 查询用）
        # 长生命周期文件句柄（Writer 持有至 close），不能配 context manager
        self._file = open(self._jsonl, "ab")  # noqa: SIM115
        self._lock = threading.Lock()
        self._model: str | None = None  # provider 模型名（选定后经 set_model 注入）
        self._first = True  # 首个写入的消息带 model 字段
        self._closed = False

    @classmethod
    def open_existing(cls, session_dir: str) -> Writer:
        """以追加模式打开已有会话目录（/resume 恢复场景，F22）。

        不创建目录；已有内容的文件不再写 model 字段（_first 置 False）。
        """
        writer = cls.__new__(cls)
        writer._jsonl = str(Path(session_dir) / "conversation.jsonl")
        writer.path = writer._jsonl  # 会话存档文件绝对路径（ch10 /session 查询用）
        # 长生命周期文件句柄（Writer 持有至 close），不能配 context manager
        writer._file = open(writer._jsonl, "ab")  # noqa: SIM115
        writer._lock = threading.Lock()
        writer._model = None
        writer._first = False
        writer._closed = False
        return writer

    def set_model(self, model: str) -> None:
        """延迟注入模型名（provider 在 TUI 内选定后才可调用，R9）。"""
        self._model = model

    def _write(self, entry: Entry) -> None:
        """序列化 + 加锁写盘 + flush + fsync（F15）。"""
        line = (json.dumps(asdict(entry), ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self._file.write(line)
            self._file.flush()
            os.fsync(self._file.fileno())

    def append(self, msg: Message, model: str | None, is_first: bool) -> None:
        """追加一条消息；`is_first` 时填充 `model` 字段并置 `_first=False`。"""
        self._write(_msg_to_entry(msg, model, is_first))
        if is_first:
            self._first = False

    def write_compact_marker(self) -> None:
        """写入 `{"type":"compact","ts":<unix_ts>}` 标记行（F12）。"""
        self._write(Entry(type="compact", ts=int(time.time())))

    def append_all(self, msgs: list[Message]) -> None:
        """逐条追加新消息（压缩替换后全量重写，F12）。"""
        for m in msgs:
            self.append(m, self._model, self._first)

    def on_append(self, msg: Message) -> None:
        """Conversation 回调：每次追加消息后写单条（F13/F44）。"""
        self.append(msg, self._model, self._first)

    def on_replace(self, msgs: list[Message]) -> None:
        """Conversation 回调：整体替换后写 compact 标记 + 全量追加（F12/F44）。"""
        self.write_compact_marker()
        self.append_all(msgs)

    def close(self) -> None:
        if not self._closed:
            self._file.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()
