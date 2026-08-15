"""Skill 专属工具：把 ToolSpec 适配为 Tool 实现（ch11 T15）。

tool 包不反向依赖 skills 包，故 `ToolSpec` 字段在此打散为工厂函数参数。
exec 固定 30s 超时，stdin 传 JSON 参数，stdout 作结果（N12）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import Result, Tool


def new_skill_tool(
    name: str,
    description: str,
    input_schema: dict,
    command: list[str],
    base_dir: Path,
) -> Tool:
    """构造一个 Skill 专属工具（asyncio subprocess exec，30s 超时）。"""
    return _SkillTool(name, description, input_schema, command, base_dir)


class _SkillTool:
    """可执行脚本型工具：cwd 固定为 Skill 目录，stdin=JSON 参数，stdout=结果。"""

    read_only = False  # 专属脚本可能有副作用：串行 + Plan Mode 不放行
    is_system = False  # 非系统工具：受 allowed_tools 约束

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        command: list[str],
        base_dir: Path,
    ) -> None:
        self._name = name
        self._description = description
        self._schema = input_schema
        self._command = list(command)
        self._base = Path(base_dir)

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict:
        return self._schema

    async def execute(self, args: str) -> Result:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                cwd=self._base,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=(args or "{}").encode("utf-8")), timeout=30.0
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return Result(is_error=True, content=f"工具 {self._name} 执行超时（30s）")
            text = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                return Result(is_error=True, content=text or err or f"exit code {proc.returncode}")
            return Result(content=text)
        except FileNotFoundError:
            return Result(is_error=True, content=f"工具 {self._name}: 可执行文件不存在")
        except Exception as e:  # noqa: BLE001 兜底转结构化错误
            return Result(is_error=True, content=f"工具 {self._name} 异常: {e}")
