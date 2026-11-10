"""in-process 后端（ch15 T13，spec F18）：同进程 asyncio task 跑队员。"""

from __future__ import annotations

from ...tool.ctx import with_cwd
from ..types import BackendType
from . import SpawnRequest


class InProcessBackend:
    """队员与主 Agent 同进程，共享 task.Manager。"""

    def __init__(self, task_mgr) -> None:
        self._task_mgr = task_mgr

    def type(self) -> BackendType:
        return BackendType.IN_PROCESS

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """调 task_mgr.launch 起 asyncio task；返回 ("", task_id)。"""
        if self._task_mgr is None:
            raise RuntimeError("in-process backend 需要 task_mgr")
        # launch creates the child's asyncio task. Capture its own worktree cwd
        # in that task's ContextVar snapshot, not the lead's checkout cwd.
        with with_cwd(req.worktree_path):
            agent_id = await self._task_mgr.launch(
                req.sub_agent, req.conv, req.member_name, req.initial_prompt
            )
        return "", agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        return  # 同进程，下一轮 Loop 自动读邮箱（no-op，spec F18）

    async def kill(self, pane_id: str, agent_id: str) -> None:
        if self._task_mgr is not None:
            await self._task_mgr.stop(agent_id)
