"""邮箱消息类型（ch15 T6，spec F32）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MessageType(StrEnum):
    """结构化消息类型（spec G9）。"""

    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


@dataclass
class Message:
    """一条邮箱消息；`from_` 序列化对应 json key `"from"`。"""

    from_: str
    to: str
    type: MessageType
    summary: str
    content: str
    payload: dict[str, Any] | None = None
    timestamp: int = 0
    read: bool = False

    def to_dict(self) -> dict:
        return {
            "from": self.from_,
            "to": self.to,
            "type": str(self.type),
            "summary": self.summary,
            "content": self.content,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "read": self.read,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        try:
            mtype = MessageType(str(d.get("type") or "text"))
        except ValueError:
            mtype = MessageType.TEXT
        return cls(
            from_=str(d.get("from") or ""),
            to=str(d.get("to") or ""),
            type=mtype,
            summary=str(d.get("summary") or ""),
            content=str(d.get("content") or ""),
            payload=d.get("payload"),
            timestamp=int(d.get("timestamp") or 0),
            read=bool(d.get("read", False)),
        )
