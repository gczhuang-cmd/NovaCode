"""Hook 生命周期挂钩系统：事件分派 + 动作执行（ch12）。

对外接口：`load`（加载 YAML 规则）、`Engine`（事件分派）、`Event`、`DispatchResult`。
"""

from __future__ import annotations

from .engine import DispatchResult, Engine
from .event import BLOCKING_EVENTS, Event, is_blocking, parse_event
from .loader import load
from .rule import Action, ActionType, Condition, Payload, Rule

__all__ = [
    "BLOCKING_EVENTS",
    "Action",
    "ActionType",
    "Condition",
    "DispatchResult",
    "Engine",
    "Event",
    "Payload",
    "Rule",
    "is_blocking",
    "load",
    "parse_event",
]
