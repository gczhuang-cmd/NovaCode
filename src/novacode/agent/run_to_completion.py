"""run_to_completion：子 Agent「跑到底」循环（ch13 T15，spec F9）。

复用主对话 `Agent.run` 的同一段 ReAct 循环（`async for` 收集事件流），不重复实现：
hook 调度 / 上下文管理 / 权限判定全部复用。区别：
- mode 用子 Agent 的 `permission_mode`（None 时 DEFAULT）；
- 把 tool / usage 事件转发到外部 events 队列（TaskManager 聚合用）；
- 触达 max_turns 抛 MaxTurnsReached（携带最后一条 assistant 文本）。

模块顶部不 import agent 包（避免模块级循环），引用常量用函数内延迟导入。
"""

from __future__ import annotations

import asyncio

from ..llm import Message


class MaxTurnsReached(Exception):
    """触达子 Agent max_turns 上限；携带最后一条 assistant 文本（spec F9）。"""

    def __init__(self, final_text: str = "") -> None:
        super().__init__("subagent max_turns reached")
        self.final_text = final_text


def _last_assistant_text(msgs: list[Message]) -> str:
    """逆序找第一条有内容的 assistant 消息文本。"""
    for m in reversed(msgs):
        if m.role == "assistant" and m.content:
            return m.content
    return ""


async def run_to_completion(
    self,
    conv,
    task: str = "",
    events: asyncio.Queue | None = None,
) -> str:
    """执行子 Agent 的"跑到底"循环，返回最后一条 assistant 文本。

    - `task` 非空才 add_user（Fork 场景 conv 已装填 Boilerplate，调用方传 "" 避免重复）；
    - mode 用 `self._permission_mode or Mode.DEFAULT`；
    - 复用 `self.run` 的事件流，把 tool / usage 事件转发到 events 队列；
    - 收到 NOTICE_MAX_ITER（触达上限）抛 MaxTurnsReached。
    """
    if task:
        conv.add_user(task)
    # 函数内延迟导入：避免模块级循环（agent/__init__ 末尾挂载本函数）
    from .. import agent as _agent

    mode = self._permission_mode or _agent.Mode.DEFAULT
    cancel = asyncio.Event()
    reached_max = False
    async for ev in self.run(conv, mode, cancel):
        if events is not None:
            if ev.tool is not None:
                await events.put(ev.tool)
            if ev.usage is not None:
                await events.put(ev.usage)
        if ev.notice == _agent.NOTICE_MAX_ITER:
            reached_max = True
        if ev.done:
            break
    final = _last_assistant_text(conv.messages())
    if reached_max:
        raise MaxTurnsReached(final)
    return final
