"""Hook 规则数据结构：Event / Condition / Action / Rule / Payload（ch12 F8/F11-F14/F16）。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from ..permission.matcher import Matcher
from .event import Event


class CombineMode(str, enum.Enum):
    """多条件组合方式：all_of / any_of 二选一，不嵌套混用（F11）。"""

    ALL_OF = "all_of"
    ANY_OF = "any_of"


class ActionType(str, enum.Enum):
    """动作类型（F16）。"""

    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"


@dataclass
class AtomCondition:
    """单个原子条件：payload 字段路径 + 匹配器（F12/F13）。"""

    field: str  # 形如 "tool_input.path"
    matcher: Matcher  # 复用 permission.Matcher


@dataclass
class Condition:
    """条件表达式：all_of / any_of 组合原子条件（F11/F12）。"""

    mode: CombineMode
    atoms: list[AtomCondition]


@dataclass
class ShellAction:
    """shell 动作：命令串（F17）。"""

    command: str


@dataclass
class PromptAction:
    """prompt 动作：注入 reminder 的文本（F20）。"""

    text: str


@dataclass
class HttpAction:
    """http 动作：URL / method / headers / body 模板（F23）。"""

    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None  # 模板字符串，None 表示用 payload JSON


@dataclass
class SubagentAction:
    """subagent 动作：本期占位（F26）。"""

    agent_name: str
    prompt: str


@dataclass
class Action:
    """动作对象：type + 各类型子字段（按 type 择一非空）。"""

    type: ActionType
    shell: ShellAction | None = None
    prompt: PromptAction | None = None
    http: HttpAction | None = None
    subagent: SubagentAction | None = None


@dataclass
class Rule:
    """一条 hook 规则：事件 + 条件 + 动作 + 执行控制（F8）。"""

    name: str
    event: Event
    action: Action
    condition: Condition | None = None  # None 表示无条件（F11）
    only_once: bool = False
    asyncio_mode: bool = False  # 对应 YAML 的 `async`（避免与关键字冲突）
    timeout_s: float = 30.0  # 命令 / HTTP 最大执行时长（F18/F24）
    source: str = ""  # 来源文件路径，供 /hooks 显示


# Payload 是事件分派时携带的上下文数据；条件求值与动作输入都用它。
# 序列化为 JSON 时保证 key 字典序（N6）用 json.dumps(payload, sort_keys=True)。
Payload = dict[str, Any]
