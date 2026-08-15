"""slash 命令体系：命令注册中心、分发解析、内置命令（ch10）。

对外接口：`Command` / `CommandContext` / `Handler` / `Kind` / `Registry` /
`parse` / `register_builtins`。
"""

from __future__ import annotations

from .builtins import register_builtins
from .command import Command, Handler, Kind
from .context import CommandContext
from .dispatch import parse
from .registry import Registry

__all__ = [
    "Command",
    "CommandContext",
    "Handler",
    "Kind",
    "Registry",
    "parse",
    "register_builtins",
]
