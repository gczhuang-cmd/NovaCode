"""edit_file 工具：对文件原文片段做唯一匹配替换。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import Result
from .ctx import resolve_path  # ch14：ctx cwd 注入（Worktree 隔离）


class EditFileTool:
    """改文件：对 `old_string` 做唯一匹配替换；匹配 0 次或多于 1 次时返回可区分错误。"""

    read_only = False  # 有副作用：串行执行、Plan Mode 不放行
    is_system = False  # ch11 T14：非系统工具

    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return (
            "对文件的原文片段做唯一匹配替换。old_string 必须唯一匹配；匹配 0 次或多于 1 次会返回错误，"
            "请提供更长上下文使其唯一。编辑前请先用 read_file 读取目标文件，确认 old_string 唯一。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要修改的文件路径"},
                "old_string": {
                    "type": "string",
                    "description": "要匹配的原文片段（必须在文件中唯一）",
                },
                "new_string": {"type": "string", "description": "替换后的新文片段"},
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, args: str) -> Result:
        """读文件 → 统计匹配数 → 唯一时替换写回；0/多情形返回可区分错误。"""
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        path = data.get("path")
        old = data.get("old_string")
        new = data.get("new_string")
        if not path:
            return Result(is_error=True, content="缺少参数 path")
        if old is None:
            return Result(is_error=True, content="缺少参数 old_string")
        if new is None:
            return Result(is_error=True, content="缺少参数 new_string")
        p = Path(resolve_path(path))  # ch14：相对路径按 ctx cwd 解析
        try:
            content = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Result(is_error=True, content=f"文件不存在: {path}")
        except PermissionError:
            return Result(is_error=True, content=f"文件不可读: {path}")
        except OSError as e:
            return Result(is_error=True, content=f"读取失败: {path}: {e}")
        n = content.count(old)
        if n == 0:
            return Result(is_error=True, content="未找到匹配的内容")
        if n > 1:
            return Result(
                is_error=True, content=f"匹配到 {n} 处，old_string 不唯一，请提供更长上下文使其唯一"
            )
        try:
            p.write_text(content.replace(old, new, 1), encoding="utf-8")
        except OSError as e:
            return Result(is_error=True, content=f"写入失败: {path}: {e}")
        return Result(content=f"已替换 {path} 中的 1 处匹配")
