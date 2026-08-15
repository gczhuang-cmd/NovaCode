"""tmux 后端（ch15 T11，spec F15/F16）。"""

from __future__ import annotations

import asyncio
import os

from ..types import BackendType
from . import SpawnRequest, build_member_cmd


class TmuxBackend:
    """通过 tmux pane 启动队员子进程。"""

    def type(self) -> BackendType:
        return BackendType.TMUX

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        cmd = build_member_cmd(req)
        if os.environ.get("TMUX"):
            # 已在 tmux 会话内：横向 split 出新 pane，-P 打印 pane id，-F 指定格式
            argv = ["tmux", "split-window", "-h", "-P", "-F", "#{pane_id}", "--", cmd]
        else:
            # tmux 会话外但二进制可用：detached 新会话（F16）；失败抛错不静默回退
            argv = ["tmux", "new-session", "-d", "-P", "-F", "#{pane_id}", "--", cmd]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"tmux spawn 失败: {err.decode('utf-8', 'replace').strip()}")
        pane_id = out.decode("utf-8", "replace").strip()
        if not pane_id:
            raise RuntimeError("tmux spawn 未返回 pane_id")
        return pane_id, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """send-keys 回车触发子进程 stdin reader 立刻去 mailbox 轮询（F15）。"""
        await asyncio.create_subprocess_exec(
            "tmux",
            "send-keys",
            "-t",
            pane_id,
            "",
            "Enter",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """kill-pane；pane 不存在忽略错误（F15）。"""
        if not pane_id:
            return
        await asyncio.create_subprocess_exec(
            "tmux",
            "kill-pane",
            "-t",
            pane_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
