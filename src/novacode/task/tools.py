"""4 个后台任务工具（ch13 T23，spec F20）。

`is_system=False`：元工具，子 Agent 构造时经 `ALL_AGENT_DISALLOWED_TOOLS` 剔除
（子 Agent 不能操控主 Agent 的后台任务 / 给别的后台 Agent 发指令）。
"""

from __future__ import annotations

import json

from ..tool import Result
from .manager import TaskBusy, TaskNotFound


def _parse_args(args: str) -> dict:
    """解析 raw JSON 参数；失败返回 {__error__: ...}。"""
    try:
        data = json.loads(args or "{}")
    except json.JSONDecodeError as e:
        return {"__error__": f"参数解析失败: {e}"}
    return data if isinstance(data, dict) else {"__error__": "参数应为 JSON 对象"}


def _task_summary(bt) -> dict:
    """TaskList 用：简要字段。"""
    return {
        "id": bt.id,
        "name": bt.name,
        "status": str(bt.status),
        "tool_count": bt.tool_count,
        "last_activity": bt.last_activity,
    }


def _task_full(bt) -> dict:
    """TaskGet 用：完整字段。"""
    return {
        "id": bt.id,
        "name": bt.name,
        "status": str(bt.status),
        "task": bt.task,
        "result": bt.result,
        "err": str(bt.err) if bt.err is not None else None,
        "start_time": bt.start_time,
        "end_time": bt.end_time,
        "tool_count": bt.tool_count,
        "last_activity": bt.last_activity,
        "usage": {
            "input": bt.usage.input,
            "output": bt.usage.output,
            "cache_write": bt.usage.cache_write,
            "cache_read": bt.usage.cache_read,
        },
    }


class TaskListTool:
    """列出当前所有后台任务。"""

    read_only = True
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TaskList"

    def description(self) -> str:
        return "列出当前所有后台任务（id/name/status/tool_count/last_activity）。"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        tasks = [_task_summary(t) for t in self._mgr.list()]
        return Result(content=json.dumps(tasks, ensure_ascii=False))


class TaskGetTool:
    """查看指定任务的完整状态。"""

    read_only = True
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TaskGet"

    def description(self) -> str:
        return "查看指定后台任务的完整状态（含 result / err / usage）。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 id"}},
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        data = _parse_args(args)
        if "__error__" in data:
            return Result(is_error=True, content=data["__error__"])
        task_id = data.get("task_id")
        if not task_id:
            return Result(is_error=True, content="missing task_id")
        bt = self._mgr.get(task_id)
        if bt is None:
            return Result(is_error=True, content=f"unknown task: {task_id}")
        return Result(content=json.dumps(_task_full(bt), ensure_ascii=False))


class TaskStopTool:
    """触发取消一个后台任务。"""

    read_only = False
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TaskStop"

    def description(self) -> str:
        return "触发取消一个后台任务；任务协程被 cancel，状态变 cancelled。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 id"}},
            "required": ["task_id"],
        }

    async def execute(self, args: str) -> Result:
        data = _parse_args(args)
        if "__error__" in data:
            return Result(is_error=True, content=data["__error__"])
        task_id = data.get("task_id")
        if not task_id:
            return Result(is_error=True, content="missing task_id")
        ok = await self._mgr.stop(task_id)
        if not ok:
            return Result(is_error=True, content=f"unknown task: {task_id}")
        return Result(content=json.dumps({"status": "cancellation_requested"}))


class SendMessageTool:
    """给仍存活（COMPLETED）的后台 Agent 续派任务。"""

    read_only = False
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "SendMessage"

    def description(self) -> str:
        return (
            "给一个已跑完的【后台子 Agent】（task 系列，非团队）发送新任务继续跑一轮。"
            "注意：Team 队员通信请用 TeamSendMessage，不要用本工具。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "后台 Agent 的名字（launch 时 name 字段）",
                },
                "message": {"type": "string", "description": "新任务指令"},
            },
            "required": ["name", "message"],
        }

    async def execute(self, args: str) -> Result:
        data = _parse_args(args)
        if "__error__" in data:
            return Result(is_error=True, content=data["__error__"])
        name = data.get("name")
        message = data.get("message")
        if not name:
            return Result(is_error=True, content="missing name")
        if not message:
            return Result(is_error=True, content="missing message")
        try:
            task_id = await self._mgr.send_message(name, message)
        except (TaskNotFound, TaskBusy) as e:
            return Result(is_error=True, content=str(e))
        return Result(content=json.dumps({"task_id": task_id, "status": "resumed"}))
