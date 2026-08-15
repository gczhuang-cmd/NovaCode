"""内置危险命令黑名单：启发式正则集，不可配置、不可绕过（N1）。

这是**启发式防御而非完备保证**——明确不追求穷尽所有危险命令；防御纵深由
沙箱（文件类）+ 规则 + 模式兜底 + 人在回路补齐。黑名单内置固定，用户不可
增删、不可关闭：任何规则、任何模式（含 bypassPermissions）都碰不到它。
"""

from __future__ import annotations

import re

# 模块级编译好的正则集：匹配命令串（Bash 工具的 command 字段）。
# 每一组都覆盖一类已知高危命令（递归强删根/家目录、写块设备、fork 炸弹、
# 格式化文件系统、重定向覆盖磁盘设备、递归改根目录权限等）。
_BLACKLIST: list[re.Pattern[str]] = [
    # 递归强删根 / 家目录（含 /*、~/*、-- 变体）
    re.compile(r"rm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(?:--\s+)?(?:/|~|\$HOME)(?:/\*|\*)?(?:\s|$)"),
    # 显式 no-preserve-root（无论目标为何都高危）；`--` 前无单词边界，故不用 `\b`
    re.compile(r"rm\s+.*--no-preserve-root\b"),
    # 写块设备：dd of=/dev/sdX 等
    re.compile(r"dd\s+.*\bof=\s*/dev/"),
    # fork 炸弹：函数体内后台管道递归（:(){ :|:& };: 及变体）
    re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\s*\(\)\s*\{\s*[^{}]*\|[^{}]*&[^{}]*\}"),
    # 格式化文件系统：mkfs.ext4 / mkfs 等
    re.compile(r"mkfs(?:\.\w+)?"),
    # 重定向覆盖磁盘设备
    re.compile(r">\s*/dev/(?:sd|hd|vd|nvme|mmcblk|disk|loop)"),
    # 递归改根目录权限为 777
    re.compile(r"chmod\s+-R\s+0?777\s+/"),
]


def hits_blacklist(command: str) -> bool:
    """命令串命中任一黑名单模式即返回 True。"""
    return any(p.search(command) for p in _BLACKLIST)
