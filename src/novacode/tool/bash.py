"""bash 工具：在 shell 中执行命令，返回 stdout / stderr / 退出码。

ch11 修复（Windows 适配）：Windows 上 `asyncio.create_subprocess_shell` 走 cmd.exe，
- description 按平台告知 AI 用 cmd/PowerShell 语法（dir/type/findstr），避免 AI 写
  bash 专属命令（ls/tail/find）全失败；
- 输出按系统 ANSI 编码解码（中文 Windows 为 GBK），避免乱码。
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from . import Result, _truncate
from .ctx import resolve_path  # ch14：ctx cwd 注入（Worktree 隔离）


def _decode(data: bytes) -> str:
    """按平台解码子进程输出：Windows cmd 默认系统 ANSI（中文系统 GBK），失败回退 UTF-8。"""
    if sys.platform == "win32":
        try:
            return data.decode("gbk")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")
    return data.decode("utf-8", errors="replace")


class BashTool:
    """执行命令：`asyncio.create_subprocess_shell` 执行，超时/取消时 kill 子进程。"""

    read_only = False  # 可执行任意副作用命令：保守归有副作用、串行执行
    is_system = False  # ch11 T14：非系统工具

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        if sys.platform == "win32":
            # Windows 上 create_subprocess_shell 走 cmd.exe，必须告知 AI 用 cmd 语法
            return (
                "在 Windows 的 cmd.exe 中执行命令，返回标准输出、标准错误与退出码。"
                "注意：本机是 Windows cmd 环境（不是 bash）——请用 cmd / PowerShell 语法"
                "（dir / type / findstr / powershell -Command ...），不要使用 bash 专属命令"
                "（ls / tail / find / grep / head 等）。非零退出不视为错误，由结果内容判断。"
                "读文件、找文件、搜内容请优先用 read_file / glob / grep 工具，不要用 bash 拼凑。"
            )
        return (
            "在 shell 中执行命令，返回标准输出、标准错误与退出码。非零退出不视为错误，由结果内容判断。"
            "读文件、找文件、搜内容请优先用 read_file / glob / grep，不要用 bash 拼凑。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        }

    async def execute(self, args: str) -> Result:
        """解析 command 并执行；超时由外层 Registry 的 wait_for 控制（取消时 kill 子进程）。"""
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        cmd = data.get("command")
        if not cmd:
            return Result(is_error=True, content="缺少参数 command")
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=resolve_path(""),  # ch14：子进程 cwd 用 ctx cwd（空串解析为进程 cwd）
        )
        try:
            stdout_b, stderr_b = await proc.communicate()
        except BaseException:
            proc.kill()
            try:
                await proc.communicate()  # 收尸，避免僵尸进程
            except Exception:  # noqa: BLE001, S110 收尸失败可忽略
                pass
            raise
        stdout = _decode(stdout_b)
        stderr = _decode(stderr_b)
        text = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        return Result(content=_truncate(text, 10000, 30000))
