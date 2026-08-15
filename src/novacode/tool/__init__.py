"""工具系统：统一工具抽象、注册中心与执行结果。

对外接口：
- `Tool`（typing.Protocol）：每个工具暴露 name / description / parameters / execute。
- `Result`：工具执行结果，永远以值类型返回（不抛 Python 异常给上层）。
- `Registry`：集中登记、按名查找、导出工具定义、带超时按名执行。
- `new_default_registry()`：注册 6 个核心工具。
- `DEFAULT_TIMEOUT`：单个工具执行的默认超时秒数（N1，不可配）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..llm import ToolDefinition

# 单个工具执行的默认超时秒数（N1，内置不可配）
DEFAULT_TIMEOUT: float = 30.0


@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""

    content: str  # 回灌给模型的文本（已截断/带行号等）
    is_error: bool = False  # True 表示结构化错误，content 即错误描述


@runtime_checkable
class Tool(Protocol):
    """统一工具抽象（F1）。"""

    def name(self) -> str: ...  # 模型看到的工具名，如 "read_file"

    def description(self) -> str: ...  # 给模型的用途说明

    def parameters(self) -> dict[str, Any]: ...  # 手写 JSON Schema（type/properties/required）

    @property
    def read_only(self) -> bool: ...  # True=只读工具（可并发执行 & Plan Mode 放行）

    @property
    def is_system(
        self,
    ) -> bool: ...  # ch11 T14：True=系统工具（LoadSkill），不受 allowed_tools 白名单约束

    async def execute(self, args: str) -> Result: ...

    # 注：args 是 raw JSON 字符串；超时由外部 asyncio.wait_for 控制


def _truncate(s: str, max_lines: int, max_chars: int) -> str:
    """截断长文本：先限字符数、再限行数；发生截断时尾部加 `[truncated]` 标注。"""
    truncated = False
    if len(s) > max_chars:
        s = s[:max_chars]
        truncated = True
    lines = s.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    result = "\n".join(lines)
    if truncated:
        result += "\n[truncated]"
    return result


class Registry:
    """集中登记、按名查找、导出定义、按名执行（F1/F5）。"""

    def __init__(self) -> None:
        self._order: list[str] = []  # 保持注册顺序，导出稳定
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        """登记一个工具；重复名称抛 ValueError（不静默覆盖）。"""
        name = t.name()
        if name in self._tools:
            raise ValueError(f"重复注册工具: {name}")
        self._tools[name] = t
        self._order.append(name)

    def get(self, name: str) -> Tool | None:
        """按名查找工具，未注册返回 None。"""
        return self._tools.get(name)

    def count(self) -> int:
        """当前已注册工具数量（O(1)，ch10 /status 数据源）。"""
        return len(self._order)

    def definitions(self) -> list[ToolDefinition]:
        """导出全部工具定义（按注册顺序），供各协议注入请求。"""
        return [
            ToolDefinition(
                name=n,
                description=self._tools[n].description(),
                input_schema=self._tools[n].parameters(),
            )
            for n in self._order
        ]

    def read_only_definitions(self) -> list[ToolDefinition]:
        """Plan Mode：仅导出只读工具（read_only==True）的定义，保留注册顺序。"""
        return [
            ToolDefinition(
                name=n,
                description=self._tools[n].description(),
                input_schema=self._tools[n].parameters(),
            )
            for n in self._order
            if self._tools[n].read_only
        ]

    def definitions_filtered(self, allowed: list[str]) -> list[ToolDefinition]:
        """按白名单切片导出：name 在 allowed 内 OR is_system 的系统工具（ch11 T14）。

        fork 模式子 Agent 用它收窄工具集；系统工具（LoadSkill 等）永远可见。
        """
        allow = set(allowed)
        return [
            ToolDefinition(
                name=n,
                description=self._tools[n].description(),
                input_schema=self._tools[n].parameters(),
            )
            for n in self._order
            if n in allow or self._tools[n].is_system
        ]

    def system_definitions(self) -> list[ToolDefinition]:
        """仅系统工具定义（ch11 T14，fork 与 allowed 合并用）。"""
        return [
            ToolDefinition(
                name=n,
                description=self._tools[n].description(),
                input_schema=self._tools[n].parameters(),
            )
            for n in self._order
            if self._tools[n].is_system
        ]

    def register_skill_tool(self, t: Tool) -> None:
        """动态注册 Skill 专属工具；重复名静默覆盖、不重复维护 order（ch11 T14/F23）。"""
        name = t.name()
        if name not in self._tools:
            self._order.append(name)
        self._tools[name] = t

    def is_read_only(self, name: str) -> bool:
        """分批判定：未知工具返回 False（按有副作用串行处理）。"""
        t = self._tools.get(name)
        return t is not None and t.read_only

    async def execute(self, name: str, args: str, timeout: float = DEFAULT_TIMEOUT) -> Result:
        """按名执行工具；未知工具 / 超时 / 异常均转结构化 Result，不抛给上层（F9/N4）。"""
        tool = self._tools.get(name)
        if tool is None:
            return Result(is_error=True, content=f"未知工具: {name}")
        try:
            return await asyncio.wait_for(tool.execute(args), timeout)
        except TimeoutError:
            return Result(is_error=True, content=f"工具 {name} 执行超时（{timeout}s）")
        except Exception as e:  # noqa: BLE001 兜底：任何异常转结构化结果，不崩溃
            return Result(is_error=True, content=f"工具 {name} 异常: {e}")


def new_default_registry() -> Registry:
    """构造并注册 6 个核心工具，返回 Registry（注册顺序稳定）。"""
    # 函数内延迟导入，避免与子模块 from . import ... 的循环引用
    from .bash import BashTool
    from .edit_file import EditFileTool
    from .glob_tool import GlobTool
    from .grep_tool import GrepTool
    from .read_file import ReadFileTool
    from .write_file import WriteFileTool

    registry = Registry()
    for t in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        BashTool(),
        GlobTool(),
        GrepTool(),
    ):
        registry.register(t)
    return registry
