"""SubAgent 角色定义文件解析（Markdown + YAML frontmatter，ch13 T2）。

参照 `skills/parser.py` 的思路独立实现一份（字段集不同，避免耦合）：
- strip BOM / 归一化 CRLF / YAMLError 转 ValueError，坏文件可被上层跳过。
- 非法 model / permissionMode 值走 stderr 警告并降级到 inherit / default（spec F7/AC20）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal, cast

import yaml

from ..permission import Mode, parse_mode
from .definition import Definition, Source

# name 命名规范（spec F4）：大小写字母开头，后续大小写字母/数字/连字符/下划线，1-32 字符。
# 与 ch13 README 的 Explore / Plan 一致（区分大小写）。
AGENT_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9\-_]{0,31}$")

_MODELS = ("inherit", "haiku", "sonnet", "opus")
ModelLiteral = Literal["haiku", "sonnet", "opus", "inherit"]


def parse_definition(data: bytes, file_path: str, source: Source) -> Definition:
    """解析一个 Agent 定义文件内容（bytes）为 Definition；字段非法抛 ValueError。"""
    text = data.decode("utf-8")
    fm, body = _split_frontmatter(text)
    return _from_meta(fm, body, file_path, source)


def parse_file(path: str, source: Source) -> Definition:
    """读取并解析一个 Agent 定义文件。"""
    return parse_definition(Path(path).read_bytes(), path, source)


def _split_frontmatter(data: str) -> tuple[dict, str]:
    """分离 `---` 包围的 frontmatter 与正文（对齐 skills/parser.py 的健壮性）。"""
    data = data.lstrip("﻿").replace("\r\n", "\n")
    if not data.startswith("---\n"):
        raise ValueError("agent definition must start with '---' frontmatter")
    end = data.find("\n---", 3)
    if end == -1:
        raise ValueError("agent definition frontmatter not closed")
    fm = data[4:end]
    body = data[end + 4 :].lstrip("\n")
    try:
        parsed = yaml.safe_load(fm)
    except yaml.YAMLError as e:
        raise ValueError(f"agent frontmatter YAML 解析失败: {e}") from e
    if not isinstance(parsed, dict):
        raise TypeError("agent frontmatter must be a YAML mapping")
    return parsed, body


def _from_meta(fm: dict, body: str, file_path: str, source: Source) -> Definition:
    """frontmatter dict → Definition（含字段校验与非法值降级）。"""
    name = str(fm.get("name") or "").strip()
    if not name or not AGENT_NAME_REGEX.fullmatch(name):
        raise ValueError(f"agent name {name!r} does not match {AGENT_NAME_REGEX.pattern}")
    description = str(fm.get("description") or "").strip()
    if not description:
        raise ValueError(f"agent {name}: description is required")
    model = _parse_model(name, str(fm.get("model") or "").strip())
    permission_mode, dont_ask = _parse_mode(name, str(fm.get("permissionMode") or "").strip())
    isolation = _parse_isolation(name, str(fm.get("isolation") or "").strip())
    return Definition(
        name=name,
        description=description,
        tools=_str_list(fm.get("tools")),
        disallowed_tools=_str_list(fm.get("disallowedTools")),
        model=model,
        max_turns=_parse_int(name, fm.get("maxTurns")),
        permission_mode=permission_mode,
        dont_ask=dont_ask,
        background=bool(fm.get("background") or False),
        isolation=isolation,
        # ch15 F48：plan_mode_required 认驼峰与下划线两种键名（对齐 ch11 坑 16 连字符/下划线）
        plan_mode_required=bool(
            fm.get("planModeRequired") or fm.get("plan_mode_required") or False
        ),
        system_prompt=body,
        file_path=file_path,
        source=source,
    )


def _str_list(v: object) -> list[str]:
    """宽松取 list[str] 字段；非列表返回空。"""
    return [str(x) for x in v] if isinstance(v, list) else []


def _parse_int(name: str, v: object) -> int:
    """宽松读整数；非法/负数返回 0（沿用全局默认）。"""
    try:
        n = int(v)  # type: ignore[call-overload]  # v 来自 YAML，运行时已由 try 兜底
    except (TypeError, ValueError):
        return 0
    return max(n, 0)


def _parse_model(name: str, s: str) -> ModelLiteral:
    """校验 model 字段；非法值 stderr 警告并降级 inherit。"""
    if s in _MODELS or not s:
        return cast(ModelLiteral, s or "inherit")
    print(f'subagent {name}: unknown model "{s}", defaulting to inherit', file=sys.stderr)
    return "inherit"


def _parse_isolation(name: str, s: str) -> str:
    """校验 isolation 字段（ch14 F20）；合法值 "" / "worktree"，非法值 stderr 警告回落空。"""
    if s in ("", "worktree"):
        return s
    print(f'subagent {name}: unknown isolation "{s}", defaulting to ""', file=sys.stderr)
    return ""


def _parse_mode(name: str, s: str) -> tuple[Mode, bool]:
    """解析 permissionMode；dontAsk 单独识别；非法值 stderr 警告并降级 default。"""
    if not s:
        return Mode.DEFAULT, False
    if s == "dontAsk":
        return Mode.DEFAULT, True
    m, ok = parse_mode(s)
    if not ok:
        print(
            f'subagent {name}: unknown permissionMode "{s}", defaulting to default',
            file=sys.stderr,
        )
        return Mode.DEFAULT, False
    return m, False
