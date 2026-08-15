"""CommandContext：handler 访问 TUI 能力的唯一通道（ch10 T4）。

从 `tui/commands.py` 迁入并扩展。字段类型用 duck typing（Any 或 novacode 层
既有类型），command 包不 import tui，避免循环依赖。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class CommandContext:
    """命令处理器所需的运行上下文。

    ch09：`conv` 变为只读 property（读取 holder.conv）——do_resume 替换 holder
    内部引用后，既有 handler 读到的 conv 自动更新。
    ch10：迁入 command 包并新增 `provider`（model 名数据源）与 `usage_box`
    （usage 可变容器，/clear 归零与 /status 读取共用）。
    """

    holder: Any  # tui.run._SessionHolder（含 conv/writer/ses_ctx，do_resume 替换其引用）
    registry: Any  # tool.Registry（工具注册中心）
    mode_box: Any  # tui.run._ModeBox（duck typing：.mode 属性）
    agent: Any  # Agent（run_force_compact / read_only_definitions）
    console: Any  # rich.console.Console（println/error 输出）
    exit_flag: list[bool]  # /exit 置 True，run 主循环据此 break
    run_callback: Callable[[], Awaitable[None]]  # /do /review 触发的「立即执行」入口
    provider: Any = None  # llm.Provider（model 名数据源）
    usage_box: Any = None  # tui.run._UsageBox（usage_in/usage_out 可变容器）
    mem_mgr: Any = None  # memory.Manager（ch09，异步记忆更新）
    runtime: Any = None  # agent.SessionRuntime（恢复时替换 session）
    root: str = ""  # 项目根目录
    sessions_dir: str = ""  # .novacode/sessions 目录
    catalog: Any = None  # skills.Catalog（ch11：/skill 与 executor 访问）
    executor: Any = None  # skills.Executor（ch11：Skill 命令 handler 经它执行）
    hook_engine: Any = None  # ch12：hook.Engine（/hooks 命令与 SessionEnd/Start/Resume 访问）
    worktree_mgr: Any = None  # ch14：worktree.Manager | None（/worktree 命令访问，duck typing）
    set_active_cwd: Any = (
        None  # ch14：Callable[[str], None] | None（/worktree enter/exit 更新主循环 active_cwd）
    )
    team_mgr: Any = None  # ch15：team.Manager | None（/team 命令访问，duck typing）

    @property
    def conv(self) -> Any:
        """当前会话历史（读 holder 中最新引用，do_resume 后自动更新）。"""
        return self.holder.conv


def hook_base_payload(*, runtime, root: str, mode, event) -> dict:
    """构造 Hook 事件 payload 的通用字段（ch12 F10）。

    `mode` 传 `permission.Mode` 实例（用 `str(mode)` 取展示名 default/plan/...）。
    """
    session_id = ""
    if runtime is not None and getattr(runtime, "session", None) is not None:
        session_id = runtime.session.session_id
    return {
        "event": event.value,
        "session_id": session_id,
        "cwd": root,
        "mode": str(mode),
    }
