"""SubAgent Catalog：三层（内置 < 用户 < 项目）角色定义目录（ch13 T6）。

同名定义按 source 优先级覆盖（后加载者覆盖前者）：builtin -> user -> project。
用户/项目级文件解析失败 stderr 警告并跳过该文件（spec F7），不阻断启动。
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import List  # noqa: UP035  # 类内方法名 `list` 遮蔽内置，用 List 注解避 mypy 误解析

from ..permission import Mode
from .definition import Definition, Source
from .embed import builtin_definitions
from .parser import parse_file


def _home() -> Path:
    """用户主目录（独立函数便于测试 monkeypatch；Windows Path.home 读 USERPROFILE）。"""
    return Path.home()


class Catalog:
    """按 name 索引的最高优先级角色定义集合。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._defs: dict[str, Definition] = {}  # name -> 最高优先级定义
        # 注：用 List 而非 list 作注解——类内方法名 `list` 会遮蔽内置 list 导致 mypy 误解析
        self._by_source: dict[Source, List[Definition]] = {}  # noqa: UP006  # 各层副本（供 /agents 类展示与 debug）

    def resolve(self, name: str) -> Definition | None:
        with self._lock:
            return self._defs.get(name)

    def list(self) -> Sequence[Definition]:
        """按 name 升序返回全部定义。"""
        with self._lock:
            return [self._defs[n] for n in sorted(self._defs)]

    def list_by_source(self, src: Source) -> Sequence[Definition]:
        with self._lock:
            return [d for d in self._by_source.get(src, [])]

    def fork_definition(self) -> Definition:
        """Fork 路径用的临时定义：name="__fork__"、工具集继承父、模式默认。"""
        return Definition(
            name="__fork__",
            description="Fork-based subagent",
            model="inherit",
            permission_mode=Mode.DEFAULT,
        )

    # ── 内部 ─────────────────────────────────────────────

    def _add_all(self, defs: List[Definition], source: Source) -> None:  # noqa: UP006
        """合并一批定义：同名时后加入者（更高优先级 source）覆盖。"""
        """合并一批定义：同名时后加入者（更高优先级 source）覆盖。"""
        with self._lock:
            self._by_source.setdefault(source, [])
            for d in defs:
                self._defs[d.name] = d
                self._by_source[source].append(d)


def load_catalog(root: str) -> Catalog:
    """按 builtin -> user -> project 顺序加载，后加载同名覆盖前者。"""
    c = Catalog()
    c._add_all(builtin_definitions(), Source.BUILTIN)
    c._add_all(_load_from_dir(_home() / ".novacode" / "agents", Source.USER), Source.USER)
    c._add_all(_load_from_dir(Path(root) / ".novacode" / "agents", Source.PROJECT), Source.PROJECT)
    return c


def _load_from_dir(dir_path: Path, source: Source) -> list[Definition]:
    """扫描一个目录下的 *.md；目录不存在返回 []；单文件解析失败 stderr 警告并跳过。"""
    if not dir_path.is_dir():
        return []
    defs: list[Definition] = []
    for p in sorted(dir_path.glob("*.md")):
        try:
            defs.append(parse_file(str(p), source))
        except (OSError, ValueError) as e:
            print(f"subagent {p.name}: skipped: {e}", file=sys.stderr)
    return defs
