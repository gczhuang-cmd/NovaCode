"""grep 工具：在文件内容中检索正则匹配，返回 `file:line:content` 命中。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from . import Result, _truncate
from .ctx import resolve_path  # ch14：ctx cwd 注入（Worktree 隔离）

# 单行超过该长度视为"过长"，显式标注而非假装无命中
_MAX_LINE = 1024 * 1024


class GrepTool:
    """搜代码内容：按 Python 正则逐行检索；结果 `file:line:content`，取前 100。"""

    read_only = True  # 只读：可并发执行 & Plan Mode 放行
    is_system = False  # ch11 T14：非系统工具

    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return "在文件内容中检索 Python 正则表达式的匹配，返回 `文件:行号:内容` 命中列表。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python 正则表达式"},
                "path": {"type": "string", "description": "搜索起始目录（默认当前目录）"},
                "glob": {"type": "string", "description": "可选文件名 glob 过滤，如 `*.py`"},
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str) -> Result:
        """解析 pattern/path/glob 并逐行扫描；正则非法、超长行、不可读文件均妥善处理。"""
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        pattern = data.get("pattern")
        if not pattern:
            return Result(is_error=True, content="缺少参数 pattern")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return Result(is_error=True, content=f"正则非法: {e}")
        root = Path(resolve_path(data.get("path") or "."))  # ch14：相对 root 按 ctx cwd 解析
        g = data.get("glob")
        hits: list[str] = []
        it = root.rglob(g) if g else root.rglob("*")
        for file in it:
            if not file.is_file():
                continue
            try:
                with open(file, encoding="utf-8", errors="replace") as f:  # noqa: ASYNC230 逐行扫描为设计行为，读文件天然阻塞
                    for lineno, line in enumerate(f, 1):
                        if len(line) > _MAX_LINE:
                            hits.append(f"{file}:{lineno}:（该行过长，未完整搜索）")
                            continue
                        if rx.search(line):
                            hits.append(f"{file}:{lineno}:{line.rstrip()}")
                        if len(hits) >= 100:
                            break
            except OSError:
                continue  # 不可读文件跳过，不视为错误
            await asyncio.sleep(0)
            if len(hits) >= 100:
                break
        if not hits:
            return Result(content="无命中")
        return Result(content=_truncate("\n".join(hits), 100, 100_000))
