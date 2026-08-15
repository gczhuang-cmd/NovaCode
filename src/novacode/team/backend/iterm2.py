"""iterm2 后端（ch15 T12，spec F17）。仅 macOS；本项目无法实跑，以命令构造单测为准。"""

from __future__ import annotations

import asyncio

from ..types import BackendType
from . import SpawnRequest, build_member_cmd


class Iterm2Backend:
    """通过 iTerm2 CLI（it2）split pane 启动队员子进程。"""

    def type(self) -> BackendType:
        return BackendType.ITERM2

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        cmd = build_member_cmd(req)
        proc = await asyncio.create_subprocess_exec(
            "it2",
            "split",
            "--new-pane",
            "--command",
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"it2 split 失败: {err.decode('utf-8', 'replace').strip()}")
        pane_id = out.decode("utf-8", "replace").strip()
        return pane_id, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        await asyncio.create_subprocess_exec(
            "it2",
            "send-text",
            "--pane",
            pane_id,
            "",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def kill(self, pane_id: str, agent_id: str) -> None:
        if not pane_id:
            return
        await asyncio.create_subprocess_exec(
            "it2",
            "close-pane",
            "--pane",
            pane_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
