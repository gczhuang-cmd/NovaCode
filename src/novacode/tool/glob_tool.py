"""glob 工具：按 glob 模式列出匹配的文件路径。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from . import Result
from .ctx import resolve_path  # ch14：ctx cwd 注入（Worktree 隔离）


class GlobTool:
    """按模式找文件：`pathlib.Path.glob` 匹配（`**` 原生支持跨层级），结果排序取前 100。"""

    read_only = True  # 只读：可并发执行 & Plan Mode 放行
    is_system = False  # ch11 T14：非系统工具

    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return "按 glob 模式列出匹配的文件路径（如 `**/*.py`）。无匹配时返回「无匹配」。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，如 `**/*.py`"},
                "path": {"type": "string", "description": "搜索起始目录（默认当前目录）"},
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str) -> Result:
        """解析 pattern/path 并遍历；每 100 个让出 event loop，取前 100 条排序。"""
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        pattern = data.get("pattern")
        if not pattern:
            return Result(is_error=True, content="缺少参数 pattern")
        root = Path(resolve_path(data.get("path") or "."))  # ch14：相对 root 按 ctx cwd 解析
        matches: list[str] = []
        try:
            for p in root.glob(pattern):
                if p.is_file():
                    matches.append(str(p))
                    if len(matches) >= 100:
                        break
                if len(matches) % 100 == 0:
                    await asyncio.sleep(0)
        except OSError as e:
            return Result(is_error=True, content=f"glob 失败: {e}")
        if not matches:
            return Result(content="无匹配")
        return Result(content="\n".join(sorted(matches)))
