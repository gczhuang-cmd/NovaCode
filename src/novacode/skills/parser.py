"""SKILL.md / tool.json 解析（ch11 T2）。

单个 Skill 解析失败抛 ValueError/FileNotFoundError，由 Catalog 捕获后跳过并 warning（F11）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

from .types import Skill, SkillMeta, SkillSource, ToolSpec

# Skill name 命名规范（F3）：小写字母开头，后续小写字母/数字/连字符。
# ch11 加固：上限放宽到 64——skills.sh/anthropics 规范允许 ~64 字符，原 32 会拒掉
# 33-64 字符的第三方 skill 名。
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_NAME_MAX = 64
# 专属工具名（tool.json）：允许下划线——实际工具 read_file/glob_tool 等带下划线，
# 专属工具要能被 allowed_tools 引用必须与之一致（F10 引用规则）
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# mode / fork_context 合法取值（F6/F7）
_MODES = ("inline", "fork")
_FORK_CONTEXTS = ("none", "recent", "full")


def parse_skill_dir(dir_path: Path, source: SkillSource) -> Skill:
    """解析一个 Skill 目录 → Skill；SKILL.md 缺失抛 FileNotFoundError（F1/F11）。"""
    skill_md = dir_path / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"no SKILL.md in {dir_path}")
    meta_dict, body = parse_frontmatter_and_body(skill_md.read_text(encoding="utf-8"))
    meta = meta_from_dict(meta_dict)
    validate_meta(meta)
    tool_specs: list[ToolSpec] = []
    tool_json = dir_path / "tool.json"
    if tool_json.is_file():
        tool_specs = parse_tool_json(tool_json.read_text(encoding="utf-8"), dir_path.resolve())
    return Skill(
        meta=meta,
        prompt_body=body,
        source_dir=dir_path.resolve(),
        source=source,
        tool_specs=tool_specs,
    )


def meta_from_dict(data: dict) -> SkillMeta:
    """从 frontmatter dict 提取已知字段（忽略未知键，F2）。

    ch11 加固：同时认 `allowed-tools`（skills.sh/anthropics 规范用连字符）与
    `allowed_tools`（下划线，本项目自定义）两种写法。
    """
    allowed = data.get("allowed_tools", data.get("allowed-tools")) or []
    if not isinstance(allowed, list):
        allowed = []
    return SkillMeta(
        name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        allowed_tools=[str(x) for x in allowed],
        mode=str(data.get("mode") or "inline"),
        fork_context=str(data.get("fork_context") or "none"),
        model=data.get("model"),
    )


def validate_meta(meta: SkillMeta) -> None:
    """校验 name/description/mode/fork_context（F3/F6/F7），非法值抛 ValueError 或 warning。"""
    if not _NAME_RE.fullmatch(meta.name) or not (1 <= len(meta.name) <= _NAME_MAX):
        raise ValueError(f"skill name {meta.name!r} does not match ^[a-z][a-z0-9-]*$ (1-32 chars)")
    if not meta.description:
        raise ValueError(f"skill {meta.name}: description is required")
    if meta.mode not in _MODES:
        print(
            f"[skill] warn: {meta.name}: unknown mode {meta.mode!r}, treated as inline",
            file=sys.stderr,
        )
        meta.mode = "inline"
    if meta.fork_context not in _FORK_CONTEXTS:
        print(
            f"[skill] warn: {meta.name}: unknown fork_context {meta.fork_context!r}, "
            "treated as none",
            file=sys.stderr,
        )
        meta.fork_context = "none"


def parse_frontmatter_and_body(data: str) -> tuple[dict, str]:
    """分离 `---` 包围的 frontmatter 与正文（F2）。

    ch11 加固（第三方 skill 兼容）：
    - strip BOM（`﻿`）——Windows PowerShell/编辑器落盘常带 BOM，源文件可能是干净的；
    - 归一化 CRLF（`\r\n` → `\n`）——Windows 文件换行不影响解析；
    - YAMLError 转 ValueError——坏 YAML 抛 ParserError 会让 Catalog 的
      `except (OSError, ValueError)` 接不住、启动崩溃，统一转成可被跳过处理的 ValueError。
    """
    data = data.lstrip("﻿").replace("\r\n", "\n")
    if not data.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' frontmatter")
    end = data.find("\n---", 3)
    if end == -1:
        raise ValueError("SKILL.md frontmatter not closed")
    fm = data[4:end]
    body = data[end + 4 :].lstrip("\n")
    try:
        parsed = yaml.safe_load(fm)
    except yaml.YAMLError as e:
        raise ValueError(f"SKILL.md frontmatter YAML 解析失败: {e}") from e
    if not isinstance(parsed, dict):
        raise TypeError("SKILL.md frontmatter must be a YAML mapping")
    return parsed, body


def parse_tool_json(data: str, base_dir: Path) -> list[ToolSpec]:
    """解析 tool.json 的 {"tools": [...]} 结构（F10）。"""
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"tool.json invalid JSON: {e}") from e
    raw = parsed.get("tools") if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        raise TypeError("tool.json must contain a 'tools' list")
    specs: list[ToolSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("tool.json: each tool must be an object")
        name = str(item.get("name") or "")
        if not _TOOL_NAME_RE.fullmatch(name):
            raise ValueError(f"tool name {name!r} invalid")
        command = item.get("command")
        if not isinstance(command, list) or not command:
            raise ValueError(f"tool {name}: command must be a non-empty array")
        specs.append(
            ToolSpec(
                name=name,
                description=str(item.get("description") or ""),
                input_schema=item.get("input_schema") or {},
                command=[str(c) for c in command],
                base_dir=base_dir,
            )
        )
    return specs
