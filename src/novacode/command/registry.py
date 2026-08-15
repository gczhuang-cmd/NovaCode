"""命令注册中心：注册/冲突检测/查找/前缀匹配/字典序排序（ch10 T2）。

`/help` 与未命中提示、自动补全菜单均以本中心为唯一数据源（G4）。
"""

from __future__ import annotations

from .command import Command


class Registry:
    """存储全部命令（主名+别名归一），提供 /help 与补全菜单的数据源。"""

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}  # 主名 + 别名都映射到同一 Command, key 已转小写
        self._visible: list[Command] = []  # 按 name 字典序，排除 hidden

    def register(self, cmd: Command) -> None:
        """注册一条命令；名或别名冲突立即 raise RuntimeError（含冲突键，F2/N4）。"""
        if not cmd.name or cmd.name != cmd.name.lower():
            raise RuntimeError(f"command name must be lowercase: {cmd.name!r}")
        for alias in cmd.aliases:
            if not alias or alias != alias.lower():
                raise RuntimeError(f"command alias must be lowercase: {alias!r}")
        for key in (cmd.name, *cmd.aliases):
            if key in self._by_name:
                raise RuntimeError(f"command conflict: {key}")
        for key in (cmd.name, *cmd.aliases):
            self._by_name[key] = cmd
        if not cmd.hidden:
            self._visible.append(cmd)
            self._visible.sort(key=lambda c: c.name)

    def lookup(self, name: str) -> Command | None:
        """按名/别名查找（内部转小写）；未命中返回 None。"""
        return self._by_name.get(name.lower())

    def visible(self) -> list[Command]:
        """返回已按 name 字典序排序的可见命令副本。"""
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        """前缀匹配可见命令名（prefix 含 "/"）；不匹配别名/描述，保持字典序（F25）。"""
        p = prefix.lstrip("/").lower()
        if not p:
            return list(self._visible)
        return [c for c in self._visible if c.name.startswith(p)]

    def remove_if(self, pred) -> None:
        """按谓词删除命令（同时清 _by_name 与 _visible，ch11 T24）。

        Skill reload 清理用：`remove_if(lambda c: c.is_skill)`。
        """
        doomed = {c.name for c in self._visible if pred(c)} | {
            k for k, c in self._by_name.items() if pred(c)
        }
        for key in doomed:
            self._by_name.pop(key, None)
        self._visible = [c for c in self._visible if not pred(c)]
