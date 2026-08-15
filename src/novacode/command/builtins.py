"""内置命令一次性注册：register_builtins（ch10 T8）。

按一致顺序注册 12 条命令；/help 的 handler 经 make_help_handler 工厂捕获 reg
（/help 输出与补全菜单共用同一注册中心，G4/N7）。
"""

from __future__ import annotations

from .builtin_hooks import handle_hooks  # ch12
from .builtin_local import (
    handle_memory,
    handle_permission,
    handle_session,
    handle_status,
    make_help_handler,
)
from .builtin_prompt import handle_do
from .builtin_skill import handle_skill
from .builtin_team import handle_team  # ch15
from .builtin_ui import handle_clear, handle_compact, handle_exit, handle_plan, handle_resume
from .builtin_worktree import handle_worktree  # ch14
from .command import Command, Kind
from .registry import Registry

__all__ = ["register_builtins"]


def register_builtins(reg: Registry) -> None:
    """注册全部 12 条内置命令（name 全小写、description 一句中文、aliases 空、hidden=False）。

    任何名字/别名重复会由 `Registry.register` 抛 RuntimeError，进程启动期即终止（F2）。
    """
    reg.register(Command("clear", "结束当前会话，开启新会话", Kind.UI, handle_clear))
    reg.register(Command("compact", "手动触发上下文压缩", Kind.UI, handle_compact))
    reg.register(Command("do", "按当前计划开始执行", Kind.PROMPT, handle_do))
    reg.register(Command("exit", "退出 NovaCode", Kind.UI, handle_exit))
    reg.register(Command("help", "查看可用命令列表", Kind.LOCAL, make_help_handler(reg)))
    reg.register(Command("hooks", "列出已加载的 hook 列表", Kind.LOCAL, handle_hooks))  # ch12
    reg.register(Command("memory", "列出已加载的记忆文件", Kind.LOCAL, handle_memory))
    reg.register(Command("permission", "查看当前权限模式", Kind.LOCAL, handle_permission))
    reg.register(Command("plan", "切换到计划模式（仅只读）", Kind.UI, handle_plan))
    reg.register(Command("resume", "恢复历史会话", Kind.UI, handle_resume))
    reg.register(Command("session", "查看当前会话信息", Kind.LOCAL, handle_session))
    reg.register(Command("skill", "列出已加载的 Skill", Kind.LOCAL, handle_skill))  # ch11
    reg.register(Command("status", "查看运行状态", Kind.LOCAL, handle_status))
    reg.register(  # ch14 F29：带参数命令（args_handler 消费 tail），handler 为 None
        Command("worktree", "管理 Worktree 隔离副本", Kind.UI, args_handler=handle_worktree)
    )
    reg.register(  # ch15 T27：/team 系列（Kind.LOCAL + args_handler）
        Command(
            "team", "管理 Agent 团队（list/info/delete/kill）", Kind.LOCAL, args_handler=handle_team
        )
    )
