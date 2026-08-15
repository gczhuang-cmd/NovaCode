"""会话层：进程内维护单会话多轮历史（user/assistant/tool 回合）。

ch09：新增可选回调 `on_append` / `on_replace`，把消息追加与整体替换同步到
JSONL 会话存档（session.Writer 注入）；`from_messages` 供 /resume 恢复场景
从已有消息列表重建会话（不触发回调、不重复写盘）。
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from .llm import ROLE_ASSISTANT, ROLE_TOOL, Message, ToolCall, ToolResult


class Conversation:
    """单会话多轮历史。退出后不持久化（ch09 由回调负责落盘）。"""

    def __init__(
        self,
        on_append: Callable[[Message], None] | None = None,
        on_replace: Callable[[list[Message]], None] | None = None,
    ) -> None:
        self._messages: list[Message] = []
        self._on_append = on_append
        self._on_replace = on_replace

    @classmethod
    def from_messages(
        cls,
        msgs: list[Message],
        on_append: Callable[[Message], None] | None = None,
        on_replace: Callable[[list[Message]], None] | None = None,
    ) -> Conversation:
        """从已有消息列表创建会话（/resume 恢复场景，ch09 F22）。

        深拷贝初始化消息列表（含 tool_calls/tool_results 子列表，不共享可变
        对象）；不触发任何回调（恢复的消息已在 JSONL 中，不重复写盘）。
        回调仍生效，供恢复后的新消息追加使用。
        """
        conv = cls(on_append=on_append, on_replace=on_replace)
        conv._messages = copy.deepcopy(msgs) if msgs else []
        return conv

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        self._messages.append(Message(role="user", content=text))
        self._maybe_append(self._messages[-1])

    def add_assistant(self, text: str) -> None:
        """追加一条助手消息。"""
        self._messages.append(Message(role="assistant", content=text))
        self._maybe_append(self._messages[-1])

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        """追加一条带工具调用的助手消息（assistant 工具调用回合）。"""
        self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))
        self._maybe_append(self._messages[-1])

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """追加一条工具结果回合（ROLE_TOOL）。"""
        self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))
        self._maybe_append(self._messages[-1])

    def _maybe_append(self, msg: Message) -> None:
        """每次追加后触发 on_append 回调（ch09 F13）。"""
        if self._on_append is not None:
            self._on_append(msg)

    def last_role(self) -> str:
        """返回最后一条消息的 role；空历史返回 ""。"""
        return self._messages[-1].role if self._messages else ""

    def length(self) -> int:
        """当前历史消息条数（usage 锚点用，ch08）。"""
        return len(self._messages)

    def replace_history(self, msgs: list[Message], notify: bool = True) -> None:
        """把内存列表整体替换为传入的 msgs（compact 用，ch08）。

        深拷贝（含 tool_calls / tool_results 子列表），不暴露原入参引用。
        ch09：`notify=True` 时触发 on_replace（写 compact 标记 + 全量追加，
        F12）。layer1 渐进替换（工具结果溢出落盘）传 `notify=False` 不写盘——
        否则每轮 AUTO 都写标记 + 全量重写，JSONL 无限膨胀；只有 layer2 摘要
        完成后的整体替换才写 compact 标记。
        """
        self._messages = copy.deepcopy(msgs) if msgs else []
        if notify and self._on_replace is not None:
            self._on_replace(list(self._messages))

    def messages(self) -> list[Message]:
        """返回历史副本，避免外部篡改内部状态。"""
        return list(self._messages)
