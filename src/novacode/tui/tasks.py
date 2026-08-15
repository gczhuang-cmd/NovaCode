"""SubAgent 后台任务通知注入（ch13 T27，spec F19）。

后台任务完成时 Manager 把 task_id push 到 done 队列；TUI 主循环每轮（收到新输入、
调 Agent.run 之前）非阻塞排空该队列，把 `<task-notification>` 拼进
`runtime.pending_reminders`——主 Agent 下一次 run 时 `take_reminders()` 自然注入
reminder 区（复用 ch12 通道，不打断当前对话）。
"""

from __future__ import annotations

import asyncio

from ..task.manager import Status


def build_task_notification(bt) -> str:
    """渲染 <task-notification> 块（spec F19 格式）。"""
    name_part = f' (name="{bt.name}")' if bt.name else ""
    if bt.status == Status.COMPLETED:
        body = f"Result: {bt.result}"
    elif bt.status == Status.FAILED:
        body = f"Error: {bt.err}"
    else:
        body = f"Status: {bt.status}"
    return (
        f"<task-notification>\nTask {bt.id}{name_part}: {bt.status}\n{body}\n</task-notification>"
    )


async def _inject_task_notifications(task_mgr, runtime) -> None:
    """非阻塞排空 done 队列，把完成通知注入 runtime.pending_reminders。"""
    q = task_mgr.subscribe_done()
    notifs: list[str] = []
    while True:
        try:
            task_id = q.get_nowait()
        except asyncio.QueueEmpty:
            break
        bt = task_mgr.get(task_id)
        if bt is None:
            continue
        notifs.append(build_task_notification(bt))
    if notifs and runtime is not None:
        runtime.append_reminders(notifs)
