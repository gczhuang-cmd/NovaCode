"""配置加载与映射：Settings YAML、友好名映射、类别判定、目标路径提取。

配置格式错误绝不导致引擎构造失败（N5）：`load_settings` 对缺失文件返回空
`Settings`；对解析失败抛 `SettingsError`，由 `new_engine` 降级跳过该文件。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..llm import ToolCall
from . import Category
from .rule import RuleSet, parse_rule


class SettingsError(Exception):
    """权限配置解析失败（调用方降级，不向上抛致命异常）。"""


@dataclass
class PermissionsBlock:
    """YAML 文件里的 permissions 块：allow / deny 两个规则串列表。"""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    """单个权限配置文件的结构（F4）。"""

    default_mode: str = ""  # 可选：default/acceptEdits/plan/bypassPermissions
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


def load_settings(path: str) -> Settings:
    """读取一个权限配置文件；文件不存在→空 Settings；解析失败→抛 SettingsError。"""
    p = Path(path)
    if not p.exists():
        return Settings()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise SettingsError(f"权限配置解析失败: {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SettingsError(f"权限配置格式错误: {path}: 顶层应为映射")
    s = Settings()
    dm = raw.get("default_mode")
    if isinstance(dm, str) and dm.strip():
        s.default_mode = dm.strip()
    perms = raw.get("permissions")
    if isinstance(perms, dict):
        for key, target in (("allow", s.permissions.allow), ("deny", s.permissions.deny)):
            entries = perms.get(key)
            if isinstance(entries, list):
                target.extend(str(x) for x in entries if isinstance(x, str))
    return s


def to_rule_set(s: Settings) -> RuleSet:
    """把 Settings 的 allow/deny 串列表解析为 RuleSet；非法条目 stderr 提示后跳过（N5，ch12 F4）。

    失败的 rule 不进入 RuleSet，但其它 rule 正常加载（F4：静默跳过 → 有声跳过）。
    """
    rs = RuleSet()
    for entry in s.permissions.allow:
        r, ok = parse_rule(entry)
        if ok:
            r.allow = True
            rs.allow.append(r)
        else:
            print(
                f"rule {entry!r} parse failed: {r.error or 'invalid syntax'}",
                file=sys.stderr,
            )
    for entry in s.permissions.deny:
        r, ok = parse_rule(entry)
        if ok:
            r.allow = False
            rs.deny.append(r)
        else:
            print(
                f"rule {entry!r} parse failed: {r.error or 'invalid syntax'}",
                file=sys.stderr,
            )
    return rs


# 友好名 ↔ 内部工具名映射（F3/AC4）
_FRIENDLY: dict[str, str] = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "glob": "Glob",
    "grep": "Grep",
}


def friendly_name(internal: str) -> str:
    """内部工具名 → 面向用户的友好名；未知工具原样返回。"""
    return _FRIENDLY.get(internal, internal)


def categorize(internal: str, read_only: bool) -> Category:
    """按内部名（+ 批类型）判定工具类别。

    `read_only == True` 一律 READ（优先于名字判定）；write_file/edit_file → WRITE；
    其余（含 bash、未知工具）→ EXEC（N7 最严：归命令执行类，触发模式 Ask）。
    """
    if read_only:
        return Category.READ
    if internal in ("write_file", "edit_file"):
        return Category.WRITE
    return Category.EXEC


def extract_target(call: ToolCall) -> tuple[str, bool, bool]:
    """从工具调用解析判定目标。

    返回 (target, is_file, ok)：is_file 由工具名决定（解析失败也归位）；
    ok=False 表示解析失败或缺必填字段——文件类走安全拒绝（DENY），命令类落模式兜底。
    """
    name = call.name
    try:
        data = json.loads(call.input or "{}")
    except (json.JSONDecodeError, TypeError):
        data = None
    if not isinstance(data, dict):
        # 解析失败：文件类仍按 is_file 归位（安全拒绝），bash 落模式兜底
        if name in ("read_file", "write_file", "edit_file", "glob", "grep"):
            return "", True, False
        return "", False, False  # bash / 未知工具
    if name in ("read_file", "write_file", "edit_file"):
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            return "", True, False  # 文件类缺 path：安全拒绝
        return path, True, True
    if name in ("glob", "grep"):
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            return ".", True, True  # 搜索根默认当前目录
        return path, True, True
    if name == "bash":
        cmd = data.get("command")
        if not isinstance(cmd, str):
            return "", False, False  # 缺 command：落模式兜底（Ask）
        return cmd, False, True
    return "", False, False  # 未知工具
