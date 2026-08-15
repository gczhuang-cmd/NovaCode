"""后台任务管理（ch13 T19~T21，spec F14~F19）。

`Manager` 管理后台子 Agent 的生命周期：launch / adopt_running / stop / send_message，
完成时把 task_id 推入 done 队列（TUI 主循环每轮排空并注入 `<task-notification>`）。
协程安全（单事件循环）。顶层不 import agent 包（避免循环），Agent 用 duck typing。
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Status(IntEnum):
    """后台任务生命周期状态（spec F15）。"""

    RUNNING = 0
    COMPLETED = 1
    FAILED = 2
    CANCELLED = 3

    def __str__(self) -> str:
        return self.name.lower()


@dataclass
class Usage:
    """后台子 Agent token 用量（对齐 agent.Usage 语义的简化版）。"""

    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class BackgroundTask:
    """一个后台子 Agent 的完整状态快照（spec F15）。"""

    id: str  # manager 生成，如 "task_<8 字节十六进制>"
    name: str  # Agent 工具 name 参数，可空
    sub_agent: Any  # Agent（duck typing，mypy 不检查动态挂载的 run_to_completion）
    conv: Any  # Conversation
    task: str  # 初始任务文本（send_message 不更新此字段）
    status: Status = Status.RUNNING
    result: str = ""  # 跑完的最终文本
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    handle: asyncio.Task | None = None  # 跑动协程的 asyncio.Task，Stop 时调 cancel()
    usage: Usage = field(default_factory=Usage)
    tool_count: int = 0
    last_activity: str = ""


@dataclass
class PartialState:
    """前台→后台移交时已收集的中间状态（spec plan PartialState）。"""

    tool_count: int = 0
    last_activity: str = ""
    usage: Usage = field(default_factory=Usage)


class TaskNotFound(Exception):
    """send_message 按 name 找不到后台任务。"""


class TaskBusy(Exception):
    """send_message 目标任务不在 COMPLETED 状态。"""


class Manager:
    """管理后台任务。协程安全（单事件循环）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}  # name -> id，后启动的覆盖
        self._done: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._counter = 0
        self._name_reg = None  # ch15：team.registry.AgentNameRegistry 委托（None 退回 _by_name）
        self._on_done: list[Callable[[str], Awaitable[None]]] = []  # ch15：on_task_done 回调

    def set_name_registry(self, reg) -> None:
        """注入 AgentNameRegistry（ch15 T21）；launch/send_message 委托解析。"""
        self._name_reg = reg

    def on_task_done(self, fn: Callable[[str], Awaitable[None]]) -> None:
        """注册任务完成回调（ch15 T21）：runner finally 时逐个 await（异常不中断）。"""
        self._on_done.append(fn)

    def _next_id(self) -> str:
        self._counter += 1
        return f"task_{secrets.token_hex(4)}"

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        """按 start_time 升序返回全部任务。"""
        return sorted(self._tasks.values(), key=lambda t: t.start_time)

    def subscribe_done(self) -> asyncio.Queue[str]:
        return self._done

    async def launch(self, ag, conv, name: str, task: str) -> str:
        """注册后台任务并起协程跑 run_to_completion；返回 task_id（spec F16）。"""
        task_id = self._next_id()
        bt = BackgroundTask(id=task_id, name=name, sub_agent=ag, conv=conv, task=task)
        async with self._lock:
            self._tasks[task_id] = bt
            if name:
                self._by_name[name] = task_id  # 后启动覆盖前
        if self._name_reg is not None and name:
            self._name_reg.register(name, task_id)  # ch15：委托 AgentNameRegistry

        events: asyncio.Queue = asyncio.Queue(maxsize=64)
        aggregator = asyncio.create_task(self._aggregate(events, bt))

        async def runner() -> None:
            try:
                text = await ag.run_to_completion(conv, task, events)
                bt.result = text
                bt.status = Status.COMPLETED
            except asyncio.CancelledError:
                bt.status = Status.CANCELLED
                raise
            except BaseException as e:  # noqa: BLE001
                # N3：任何异常不崩主程序，转 status=failed + err
                bt.status = Status.FAILED
                bt.err = e
            finally:
                bt.end_time = time.monotonic()
                aggregator.cancel()
                self._push_done(task_id)
                await self._notify_done(task_id)  # ch15

        bt.handle = asyncio.create_task(runner())
        return task_id

    async def adopt_running(
        self,
        ag,
        conv,
        name: str,
        events: asyncio.Queue,
        handle: asyncio.Task,
        partial: PartialState,
    ) -> str:
        """前台超时切后台：注册 BackgroundTask 并接管已启动的协程（spec F17-2）。

        前台 `run_to_completion` 被 wait_for 超时 cancel 后，调用方会重新
        `create_task` 起协程（handle）并从当前 conv 状态继续；adopt 只登记状态、
        聚合事件、等 handle 完成后写终态 + push done。
        """
        task_id = self._next_id()
        bt = BackgroundTask(id=task_id, name=name, sub_agent=ag, conv=conv, task="", handle=handle)
        bt.tool_count = partial.tool_count
        bt.last_activity = partial.last_activity
        bt.usage = partial.usage
        async with self._lock:
            self._tasks[task_id] = bt
            if name:
                self._by_name[name] = task_id
        if self._name_reg is not None and name:
            self._name_reg.register(name, task_id)  # ch15

        aggregator = asyncio.create_task(self._aggregate(events, bt))

        async def runner() -> None:
            try:
                await handle
            except asyncio.CancelledError:
                bt.status = Status.CANCELLED
            except BaseException as e:  # noqa: BLE001
                bt.status = Status.FAILED
                bt.err = e
            else:
                bt.result = _last_assistant_text(conv.messages())
                bt.status = Status.COMPLETED
            finally:
                bt.end_time = time.monotonic()
                aggregator.cancel()
                self._push_done(task_id)
                await self._notify_done(task_id)  # ch15

        # bt.handle 保持 handle（run_task 本身）——stop 时 cancel 它即可终止子 Agent；
        # runner 只是包装 await handle 写终态，不覆盖 bt.handle。
        asyncio.create_task(runner())
        return task_id

    async def stop(self, task_id: str) -> bool:
        """触发取消；返回是否找到该任务（spec F20 TaskStop）。"""
        bt = self._tasks.get(task_id)
        if bt is None:
            return False
        handle = bt.handle
        if handle is None or handle.done():
            return True
        handle.cancel()
        try:
            await handle
        except BaseException:  # noqa: BLE001,S110 CancelledError / 协程内异常都在此处收尾
            pass
        # 兜底：协程从未被调度即 cancel 时，asyncio 不会执行协程体的 finally
        # （status 保持 RUNNING），手动补终态 + push done。
        if bt.status is Status.RUNNING:
            bt.status = Status.CANCELLED
            bt.end_time = time.monotonic()
            self._push_done(task_id)
        return True

    async def send_message(self, name: str, message: str) -> str:
        """给仍存活（COMPLETED）的后台 Agent 续派任务；返回 task_id（同 id 复用，spec F20）。

        仅 status==COMPLETED 允许；否则 raise TaskBusy / TaskNotFound。
        """
        task_id = None
        if self._name_reg is not None:
            task_id = self._name_reg.resolve(name)  # ch15：优先委托 AgentNameRegistry
        if task_id is None:
            task_id = self._by_name.get(name)
        if task_id is None:
            raise TaskNotFound(f"no background task named {name}")
        bt = self._tasks.get(task_id)
        if bt is None or bt.status != Status.COMPLETED:
            raise TaskBusy(
                f"task {task_id} is not completed (status={bt.status if bt else 'gone'})"
            )
        bt.conv.add_user(message)
        bt.status = Status.RUNNING
        bt.end_time = 0.0
        bt.result = ""
        bt.err = None
        events: asyncio.Queue = asyncio.Queue(maxsize=64)
        aggregator = asyncio.create_task(self._aggregate(events, bt))

        async def runner() -> None:
            try:
                text = await bt.sub_agent.run_to_completion(bt.conv, "", events)
                bt.result = text
                bt.status = Status.COMPLETED
            except asyncio.CancelledError:
                bt.status = Status.CANCELLED
            except BaseException as e:  # noqa: BLE001
                bt.status = Status.FAILED
                bt.err = e
            finally:
                bt.end_time = time.monotonic()
                aggregator.cancel()
                self._push_done(task_id)
                await self._notify_done(task_id)  # ch15

        bt.handle = asyncio.create_task(runner())
        return task_id

    async def _notify_done(self, task_id: str) -> None:
        """ch15：逐个 await on_task_done 回调（异常不中断）。"""
        for fn in self._on_done:
            try:
                await fn(task_id)
            except Exception:  # noqa: BLE001,S110 回调失败不影响任务收尾
                pass

    async def upgrade_approval(self, req: object) -> tuple[object, bool]:
        """本期默认实现：拒绝本次（spec 修订记录——不做主 TUI 弹窗）。"""
        from ..permission import Outcome

        return Outcome.DENY_ONCE, True

    # ── 内部 ─────────────────────────────────────────────

    def _push_done(self, task_id: str) -> None:
        try:
            self._done.put_nowait(task_id)
        except asyncio.QueueFull:
            print(
                f"task manager: done queue full, dropping notification for {task_id}",
                file=sys.stderr,
            )

    async def _aggregate(self, events: asyncio.Queue, bt: BackgroundTask) -> None:
        """消费 run_to_completion 转发的事件：ToolEvent(Phase.START) 累加 tool_count /
        last_activity；Usage 累加到 bt.usage；队列收到 None sentinel 退出。"""
        from ..agent import Phase  # 函数内延迟导入（task 顶层不 import agent）

        try:
            while True:
                item = await events.get()
                if item is None:
                    break
                phase = getattr(item, "phase", None)
                if phase == Phase.START:
                    bt.tool_count += 1
                    bt.last_activity = getattr(item, "name", "")
                elif hasattr(item, "input") and hasattr(item, "output"):
                    bt.usage.input += item.input
                    bt.usage.output += item.output
        except asyncio.CancelledError:
            pass


def _last_assistant_text(msgs: list) -> str:
    """逆序找第一条有内容的 assistant 消息文本。"""
    for m in reversed(msgs):
        if m.role == "assistant" and m.content:
            return m.content
    return ""
