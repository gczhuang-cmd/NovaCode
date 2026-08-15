"""Backend 抽象：Backend Protocol + SpawnRequest + new_backend 工厂 + build_member_cmd（ch15 T9）。"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from ..types import BackendType


class Backend(Protocol):
    """执行后端统一接口（spec F12）。"""

    def type(self) -> BackendType: ...

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]: ...  # (pane_id, agent_id)
    async def wake(self, pane_id: str, agent_id: str) -> None: ...
    async def kill(self, pane_id: str, agent_id: str) -> None: ...


@dataclass
class SpawnRequest:
    """后端 spawn 的完整入参（spec F13）。"""

    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str
    session_dir: str
    agent_type: str
    model: str
    initial_prompt: str
    plan_mode_required: bool

    # in-process 专用——同进程后端直接复用这三个对象
    sub_agent: Any = None  # agent.Agent
    conv: Any = None  # conversation.Conversation
    task_mgr: Any = None  # task.Manager


def build_member_cmd(req: SpawnRequest, python: str | None = None) -> str:
    """构造 `python -m novacode --team-member ...` 命令行字符串（F15/F17）。

    `--agent-id` 必须传——子进程不需要读 Lead 还没写完的 config.json 找自己。
    `initial_prompt` **不**走命令行（由 spawn_teammate 预写入 mailbox）。
    """
    py = python or sys.executable
    parts = [
        py,
        "-m",
        "novacode",
        "--team-member",
        "--team",
        req.team_name,
        "--member",
        req.member_name,
        "--agent-id",
        req.agent_id,
        "--session-dir",
        req.session_dir,
        "--worktree",
        req.worktree_path,
    ]
    if req.agent_type:
        parts += ["--agent-type", req.agent_type]
    if req.model:
        parts += ["--model", req.model]
    if req.plan_mode_required:
        parts.append("--plan-mode")
    return shlex.join(parts)


def new_backend(t: BackendType, **deps) -> Backend:
    """按类型构造 Backend 实例；in-process 需要 `task_mgr`。"""
    if t == BackendType.IN_PROCESS:
        from .inprocess import InProcessBackend

        return InProcessBackend(deps.get("task_mgr"))
    if t == BackendType.TMUX:
        from .tmux import TmuxBackend

        return TmuxBackend()
    if t == BackendType.ITERM2:
        from .iterm2 import Iterm2Backend

        return Iterm2Backend()
    raise ValueError(f"未知后端类型: {t}")
