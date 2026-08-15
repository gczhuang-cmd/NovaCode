"""Catalog：三层路径扫描与覆盖管理（ch11 T4）。

内置 < 用户 < 项目；后扫到的同名 Skill 覆盖前者（F13）。
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from . import parser
from .types import Skill, SkillSource


@dataclass
class ValidationIssue:
    """一条 fail-fast 依赖检查不通过项（F15）。"""

    skill_name: str
    tool_name: str


# 系统工具豁免：这些工具总是可用，不参与 allowed_tools 校验（T4 决议）
_SYSTEM_TOOL_NAMES = ("load_skill", "install_skill")


def _home() -> Path:
    """用户主目录（独立函数便于测试 monkeypatch；Windows Path.home 读 USERPROFILE）。"""
    return Path.home()


class Catalog:
    """三层（内置 < 用户 < 项目）Skill 目录集合。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_name: dict[str, Skill] = {}
        self._order: list[str] = []

    # ── 注册 / 查询 ─────────────────────────────────────────────

    def register(self, s: Skill) -> None:
        """注册一个 Skill；同名覆盖（_order 保持按 name 字典序且不重复）。"""
        with self._lock:
            if s.meta.name not in self._by_name:
                self._order.append(s.meta.name)
                self._order.sort()
            self._by_name[s.meta.name] = s

    def get(self, name: str) -> Skill | None:
        with self._lock:
            return self._by_name.get(name)

    def list(self) -> list[Skill]:
        with self._lock:
            return [self._by_name[n] for n in self._order]

    def names(self) -> list[str]:
        with self._lock:
            return list(self._order)

    def remove(self, name: str) -> None:
        """按名移除（fail-fast 校验剔除用，F15）。"""
        with self._lock:
            if name in self._by_name:
                del self._by_name[name]
                self._order.remove(name)

    # ── 加载 ──────────────────────────────────────────────────

    @classmethod
    def load(cls, work_dir: Path) -> Catalog:
        """按内置 → 用户 → 项目顺序扫描，后扫同名覆盖前者（F12/F13）。"""
        c = cls()
        _load_builtin_into(c)
        _load_dir_into(c, _home() / ".novacode" / "skills", SkillSource.USER)
        _load_dir_into(c, work_dir / ".novacode" / "skills", SkillSource.PROJECT)
        return c

    def reload(self, work_dir: Path) -> tuple[list[str], list[str]]:
        """重扫三层路径，原子替换内部状态，返回 (added, removed)（F17）。"""
        with self._lock:
            before = set(self._order)
            new = Catalog.load(work_dir)
            after = set(new._order)
            self._by_name = new._by_name
            self._order = new._order
        return sorted(after - before), sorted(before - after)

    # ── fail-fast 校验 ────────────────────────────────────────

    def validate_tools(self, reg) -> list[ValidationIssue]:
        """校验每个 Skill 的 allowed_tools 都能在 reg 里 get 到（F15）。

        load_skill / install_skill 视为允许引用（系统工具豁免）。
        """
        issues: list[ValidationIssue] = []
        for s in self.list():
            for t in s.meta.allowed_tools:
                if t in _SYSTEM_TOOL_NAMES:
                    continue
                if reg.get(t) is None:
                    issues.append(ValidationIssue(skill_name=s.meta.name, tool_name=t))
        return issues


def _load_dir_into(c: Catalog, base_dir: Path, source: SkillSource) -> None:
    """扫描一个文件系统目录下的直接子目录作为 Skill 候选（F14）。"""
    if not base_dir.is_dir():
        return
    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir():
            continue
        try:
            c.register(parser.parse_skill_dir(entry, source))
        except (OSError, ValueError) as e:
            print(f"[skill] warn: skip {entry}: {e}", file=sys.stderr)


def _load_builtin_into(c: Catalog) -> None:
    """通过 importlib.resources 读取内置 Skill（T5）。

    本期内置三个 Skill 均无 tool.json，直接读 SKILL.md 内容构造，不解压 cache；
    source_dir 指向包资源路径（仅用于重读 body）。
    """
    try:
        base = files("novacode.skills.builtin")
    except (ModuleNotFoundError, FileNotFoundError):
        return
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        md = entry.joinpath("SKILL.md")
        if not md.is_file():
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            continue
        try:
            meta_dict, body = parser.parse_frontmatter_and_body(text)
            meta = parser.meta_from_dict(meta_dict)
            parser.validate_meta(meta)
            c.register(
                Skill(
                    meta=meta,
                    prompt_body=body,
                    source_dir=Path(entry.name),
                    source=SkillSource.BUILTIN,
                )
            )
        except (OSError, ValueError) as e:
            print(f"[skill] warn: skip builtin {entry.name}: {e}", file=sys.stderr)
