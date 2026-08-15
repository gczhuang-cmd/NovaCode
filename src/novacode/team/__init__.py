"""Team 顶层包：多 Agent 协作小组（ch15）。

数据层（Team/TeammateInfo/Manager）、后端抽象（Backend）、协作层（mailbox/registry/tasks）、
工具层（tools）与 Coordinator Mode 协同。
"""

from .manager import LeadMessage, Manager
from .types import (
    BackendType,
    InProcessTeammateNoSpawnError,
    MemberExistsError,
    MemberNotFoundError,
    Team,
    TeamError,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)

__all__ = [
    "BackendType",
    "InProcessTeammateNoSpawnError",
    "LeadMessage",
    "Manager",
    "MemberExistsError",
    "MemberNotFoundError",
    "Team",
    "TeamError",
    "TeamHasActiveMembersError",
    "TeamNotFoundError",
    "TeammateInfo",
]
