"""write_file 工具：写入（覆盖）文件，父目录不存在时自动创建。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import Result
from .ctx import resolve_path  # ch14：ctx cwd 注入（Worktree 隔离）


class WriteFileTool:
    """写文件：给定路径与内容覆盖写入；父目录自动创建。"""

    read_only = False  # 有副作用：串行执行、Plan Mode 不放行
    is_system = False  # ch11 T14：非系统工具

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "把内容写入（覆盖）指定文件；父目录不存在时自动创建。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的完整内容"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, args: str) -> Result:
        """解析 path/content 并覆盖写入；任何 OSError 转结构化错误。"""
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        path = data.get("path")
        content = data.get("content")
        if not path:
            return Result(is_error=True, content="缺少参数 path")
        if content is None:
            return Result(is_error=True, content="缺少参数 content")
        try:
            p = Path(resolve_path(path))  # ch14：相对路径按 ctx cwd 解析
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return Result(is_error=True, content=f"写入失败: {path}: {e}")
        return Result(content=f"已写入 {path}（{len(content.encode('utf-8'))} 字节）")
