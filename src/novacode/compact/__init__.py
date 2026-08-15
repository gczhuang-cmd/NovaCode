"""上下文管理子包：第 1/2 层压缩、恢复、Token 估算、状态与熔断。

对外接口：`manage_context` / `TriggerKind` / 状态类型 / 会话上下文。
仅依赖 `novacode.llm`、`novacode.conversation` 与标准库（依赖方向无环）。
"""

from .compact import ManageInput, ManageOutput, TriggerKind, manage_context
from .layer1 import offload_and_snip
from .layer2 import auto_compact, force_compact
from .recovery import build_recovery_attachment
from .state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    FileReadRecord,
    RecoveryState,
    SessionContext,
    new_session_context,
    open_session_context,
    parse_session_time,
)
from .token import estimate_tokens, usage_anchor

__all__ = [
    "CompactCircuitBreaker",
    "ContentReplacementState",
    "FileReadRecord",
    "ManageInput",
    "ManageOutput",
    "RecoveryState",
    "SessionContext",
    "TriggerKind",
    "auto_compact",
    "build_recovery_attachment",
    "estimate_tokens",
    "force_compact",
    "manage_context",
    "new_session_context",
    "offload_and_snip",
    "open_session_context",
    "parse_session_time",
    "usage_anchor",
]
