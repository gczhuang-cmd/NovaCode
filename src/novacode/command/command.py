"""命令类型定义：Kind 枚举、Command dataclass、Handler 类型别名（ch10 T1）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from .context import CommandContext

# 命令处理函数签名：接收 CommandContext，异步完成
Handler = Callable[[CommandContext], Awaitable[None]]


class Kind(Enum):
    """命令执行类型：决定是否影响会话/是否进 history（F8）。"""

    LOCAL = "local"  # 纯本地：只打印，不改会话状态，不进 history
    UI = "ui"  # 影响界面：改会话/模式/进程状态，不进 history
    PROMPT = "prompt"  # 提示词：注入 user 消息 + 触发回合，进 history


@dataclass(slots=True)
class Command:
    """一条已注册命令的元数据与处理函数（F1）。"""

    name: str  # 不带 "/" 前缀，全小写，唯一
    description: str  # 一句话，用于 /help 与补全菜单
    kind: Kind
    handler: Handler | None = None  # 无参处理；带参命令（args_handler）可为 None（ch14）
    aliases: list[str] = field(default_factory=list)  # 不带 "/" 前缀，全小写，全局唯一(含 name)
    hidden: bool = False  # /help 与补全菜单都不显示，但 dispatcher 仍可命中（F28）
    is_skill: bool = False  # ch11 T23：Skill 自动注册的命令标记（reload 时批量移除）
    args_handler: Callable[[CommandContext, str], Awaitable[None]] | None = (
        None  # ch14 F29：带参数命令；dispatch 命中后优先于 handler
    )
