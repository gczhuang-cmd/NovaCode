"""系统提示工程化资源包。

对外接口：
- 模块化装配：`Module` / `fixed_modules` / `optional_modules` / `assemble_system` / `build_system_prompt`。
- 环境信息段（不缓存）：`Environment` / `gather_environment`。
- 补充消息注入：`system_reminder` / `plan_reminder`；`EXECUTE_DIRECTIVE`（/do 注入用户消息）。
- 启动横幅：`NOVACODE_BANNER` / `render_banner`。
"""

from __future__ import annotations

from .environment import Environment, gather_environment
from .modules import (
    Module,
    assemble_system,
    build_system_prompt,
    fixed_modules,
    optional_modules,
)
from .reminder import (
    EXECUTE_DIRECTIVE,
    PLAN_REMINDER_FULL,
    plan_reminder,
    system_reminder,
)
from .skills_block import (
    ActiveSkillEntry,
    SkillCatalogItem,
    render_active_skills_block,
    render_skills_catalog,
)

# 兼容别名（弃用）：旧 `SYSTEM_PROMPT` 常量迁移为 `build_system_prompt()` 结果。
# ch05 T6 起适配器改用 `Request`，此别名随适配器改造移除。
SYSTEM_PROMPT = build_system_prompt()
# 兼容别名（弃用）：旧 `PLAN_MODE_REMINDER` 常量（拼接在系统提示尾部）。
# ch05 T9 起 agent 改用 `plan_reminder()` 走消息通道，此别名移除。
PLAN_MODE_REMINDER = PLAN_REMINDER_FULL

__all__ = [
    "EXECUTE_DIRECTIVE",
    "NOVACODE_BANNER",
    "ActiveSkillEntry",
    "Environment",
    "Module",
    "SkillCatalogItem",
    "assemble_system",
    "build_system_prompt",
    "fixed_modules",
    "gather_environment",
    "optional_modules",
    "plan_reminder",
    "render_active_skills_block",
    "render_banner",
    "render_skills_catalog",
    "system_reminder",
]

NOVACODE_BANNER = r"""
███╗   ██╗ ██████╗ ██╗   ██╗ █████╗  ██████╗ ██████╗ ██████╗ ███████╗
████╗  ██║██╔═══██╗██║   ██║██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝
██╔██╗ ██║██║   ██║██║   ██║███████║██║     ██║   ██║██║  ██║█████╗
██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║██║     ██║   ██║██║  ██║██╔══╝
██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║╚██████╗╚██████╔╝██████╔╝███████╗
╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝"""


def render_banner(version: str, cwd: str) -> str:
    """渲染启动横幅：NOVACODE 艺术字 + 「版本 + 工作目录」一行 + 就绪提示行。

    ch10 N7：命令清单单一信源在 command.Registry，此处只允许 /help 作为入口引导，
    其他命令名不得在本模块出现。
    """
    return (
        f"{NOVACODE_BANNER}\n  NOVACODE v{version}    {cwd}\n"
        "  就绪：输入 /help 查看可用命令 · Shift+Tab 切换权限模式"
    )
