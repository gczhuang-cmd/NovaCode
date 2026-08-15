"""read_file 工具：按路径读取文件文本内容（带行号）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import Result, _truncate
from .ctx import resolve_path  # ch14：ctx cwd 注入（Worktree 隔离）


class ReadFileTool:
    """读文件：给定路径返回带行号的文件文本；不存在/不可读时返回结构化错误。"""

    read_only = True  # 只读：可并发执行 & Plan Mode 放行
    is_system = False  # ch11 T14：非系统工具

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取指定文件的文本内容（带行号，便于引用）。文件不存在、不可读或是目录时返回错误。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
            },
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        """解析 path 并读取；空 args 当 `{}`，行/字节超限截断并标注。"""
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        path = data.get("path")
        if not path:
            return Result(is_error=True, content="缺少参数 path")
        p = Path(resolve_path(path))  # ch14：相对路径按 ctx cwd 解析
        try:
            if p.is_dir():
                return Result(is_error=True, content=f"{path} 是目录，请指定文件")
            text = p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return Result(is_error=True, content=f"文件不存在: {path}")
        except PermissionError:
            return Result(is_error=True, content=f"文件不可读: {path}")
        except OSError as e:
            return Result(is_error=True, content=f"读取失败: {path}: {e}")
        numbered = [f"{n:6d}\t{line}" for n, line in enumerate(text.splitlines(), 1)]
        return Result(content=_truncate("\n".join(numbered), 2000, 256 * 1024))
