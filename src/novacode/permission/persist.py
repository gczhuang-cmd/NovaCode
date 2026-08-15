"""永久放行规则写入：把人在回路「永久」的精确 allow 规则落盘到本地层配置。

自动规则**不泛化**：只生成针对该次具体调用的精确匹配规则（命令串 / 相对路径原样），
并转义 glob 元字符，防止 `Bash(git status)` 被解读成 `Bash(git st*tus)` 之类的通配。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ..llm import ToolCall
from .engine import Engine
from .matcher import compile_matcher
from .rule import Rule
from .settings import Settings, SettingsError, extract_target, friendly_name, load_settings


def escape_glob(s: str) -> str:
    """转义 glob 元字符，使精确规则不被通配泛化。"""
    for ch in "*?[]":
        s = s.replace(ch, "\\" + ch)
    return s


def rule_for(root: str, call: ToolCall) -> tuple[Rule, str, bool]:
    """据调用生成精确 allow 规则（内存 Rule + YAML 串两种形态）。

    - bash → `Bash(<command>)`（命令串经 escape_glob 转义）；
    - 文件类 → `Read/Write/Edit(<relpath>)`（相对 root 的 slash 路径，转义元字符）；
    - MCP 工具（友好名 `mcp__<server>__<tool>`，ch07）→ `mcp__<server>__<tool>`：工具名即
      精确匹配，无 pattern 段（匹配该工具全部参数调用）；
    - 解析失败 / 未知工具 → (Rule("","",False), "", False)。
    """
    friendly = friendly_name(call.name)
    target, is_file, ok = extract_target(call)
    if ok:
        if not is_file:
            pattern = escape_glob(target)
            return (
                Rule(
                    tool=friendly,
                    matcher=compile_matcher(pattern, is_command=True),
                    allow=True,
                    raw=pattern,
                ),
                f"{friendly}({pattern})",
                True,
            )
        abs_p = (
            os.path.abspath(os.path.join(root, target))
            if not os.path.isabs(target)
            else os.path.abspath(target)
        )
        try:
            rel = os.path.relpath(abs_p, root)
        except ValueError:
            return Rule(), "", False
        rel = rel.replace(os.sep, "/")
        pattern = escape_glob(rel)
        return (
            Rule(
                tool=friendly,
                matcher=compile_matcher(pattern, is_command=False),
                allow=True,
                raw=pattern,
            ),
            f"{friendly}({pattern})",
            True,
        )
    # ok=False 但友好名是 MCP 工具：工具级精确规则（ch07 F8「永久」落盘 mcp__<server>__<tool>）
    if friendly.startswith("mcp__"):
        return Rule(tool=friendly, matcher=None, allow=True), friendly, True
    return Rule(), "", False


def persist_local_allow(engine: Engine, call: ToolCall) -> None:
    """把精确 allow 规则写入本地层文件并同步内存（去重）。失败向上抛，调用方只记日志不阻断。"""
    rule, text, ok = rule_for(engine.root, call)
    if not ok:
        return
    # 读当前本地配置（缺失→空，解析失败→空）
    settings = Settings()
    if Path(engine.local_path).exists():
        try:
            settings = load_settings(engine.local_path)
        except SettingsError:
            settings = Settings()
    if text not in settings.permissions.allow:
        settings.permissions.allow.append(text)
    # 组装 YAML 并写盘（保留 default_mode 若有）
    out: dict = {
        "permissions": {"allow": settings.permissions.allow, "deny": settings.permissions.deny}
    }
    if settings.default_mode:
        out["default_mode"] = settings.default_mode
    p = Path(engine.local_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8")
    # 同步内存 RuleSet（去重依据：tool + 原始模式段 raw）
    if not any(r.tool == rule.tool and r.raw == rule.raw for r in engine.local.allow):
        engine.local.allow.append(rule)
