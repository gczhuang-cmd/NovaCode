"""权限系统：五层防御中的前四层判定 + 配置加载 + 本地规则写入。

层级边界：
- `Engine.check` 实现**前四层**（黑名单 → 沙箱 → 规则引擎 → 模式兜底），
  返回 `Ask` 作为「请走第五层」的信号；
- **第五层（人在回路）由 agent 在 Ask 后编排驱动**（发 `ApprovalRequest` 事件、
  await 用户决策）。

对外门面：`Mode`/`Decision`/`Category`/`Outcome`、`Engine`/`new_engine`/
`check`/`persist_local_allow`。与 provider 协议无关（N6），不 import agent/tui。
"""

from __future__ import annotations

from enum import IntEnum

# ─────────────────────────────────────────────────────────────────────────────
# 基础类型（定义在 __init__ 顶部，供子模块 `from . import ...` 引用）
# ─────────────────────────────────────────────────────────────────────────────


class Mode(IntEnum):
    """权限模式：四档。"""

    DEFAULT = 0  # 只读 Allow / 文件写 Ask / 命令执行 Ask
    ACCEPT_EDITS = 1  # 文件写 Allow / 命令执行 Ask
    PLAN = 2  # 仅只读工具可见（沿用 ch04）；矩阵同 default 作防御兜底
    BYPASS = 3  # 全 Allow（黑名单 / 沙箱仍拦）

    def __str__(self) -> str:
        """面向展示的模式名：default / acceptEdits / plan / bypassPermissions。"""
        return _MODE_STR[self]


_MODE_STR: dict[Mode, str] = {
    Mode.DEFAULT: "default",
    Mode.ACCEPT_EDITS: "acceptEdits",
    Mode.PLAN: "plan",
    Mode.BYPASS: "bypassPermissions",
}

# 大小写不敏感的模式名 → 枚举（含常见别名）
_MODE_NAMES: dict[str, Mode] = {
    "default": Mode.DEFAULT,
    "acceptedits": Mode.ACCEPT_EDITS,
    "accept_edits": Mode.ACCEPT_EDITS,
    "plan": Mode.PLAN,
    "bypasspermissions": Mode.BYPASS,
    "bypass": Mode.BYPASS,
}


def parse_mode(s: str) -> tuple[Mode, bool]:
    """大小写不敏感识别四档名；未知返回 (Mode.DEFAULT, False)。"""
    m = _MODE_NAMES.get((s or "").strip().lower())
    if m is None:
        return Mode.DEFAULT, False
    return m, True


class Decision(IntEnum):
    """判定结果：Allow / Deny / Ask。"""

    ALLOW = 0
    DENY = 1
    ASK = 2


class Category(IntEnum):
    """工具类别：只读 / 文件写 / 命令执行。"""

    READ = 0
    WRITE = 1
    EXEC = 2


class Outcome(IntEnum):
    """人在回路三选一结果（TUI 回传给 agent）。"""

    DENY_ONCE = 0  # 拒绝本次（回灌让模型调整）
    ALLOW_ONCE = 1  # 允许本次（不留规则）
    ALLOW_FOREVER = 2  # 永久允许（+写本地层精确规则）


# ─────────────────────────────────────────────────────────────────────────────
# 子模块装配（枚举已在上面定义，子模块 `from . import ...` 无循环）
# ─────────────────────────────────────────────────────────────────────────────

from .blacklist import hits_blacklist
from .engine import Engine, check, mode_fallback, new_engine, start_mode
from .persist import escape_glob, persist_local_allow, rule_for
from .rule import Rule, RuleSet, match_pattern, parse_rule
from .sandbox import eval_symlinks_or_ancestor, resolve_root, sandbox_ok
from .settings import (
    PermissionsBlock,
    Settings,
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)

__all__ = [
    "Category",
    "Decision",
    "Engine",
    "Mode",
    "Outcome",
    "PermissionsBlock",
    "Rule",
    "RuleSet",
    "Settings",
    "SettingsError",
    "categorize",
    "check",
    "escape_glob",
    "eval_symlinks_or_ancestor",
    "extract_target",
    "friendly_name",
    "hits_blacklist",
    "load_settings",
    "match_pattern",
    "mode_fallback",
    "new_engine",
    "parse_mode",
    "parse_rule",
    "persist_local_allow",
    "resolve_root",
    "rule_for",
    "sandbox_ok",
    "start_mode",
    "to_rule_set",
]
