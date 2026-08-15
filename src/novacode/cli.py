"""NovaCode 命令行入口：加载配置、装配 MCP 客户端、装配权限引擎、启动对话。

启动流程：`config.load(".novacode/config.yaml")` → 加载项目指令与记忆索引 →
`mcp.load_config(root)` / `mcp.new_manager(...)`（注册 MCP 工具）→
`permission.new_engine(root)` → 建 SessionRuntime / session.Writer → 后台过期清理 →
`asyncio.run(tui.run(...))`；退出时 await 后台清理与记忆更新 task 收尾、
`finally: await mgr.close()` 统一关闭全部 MCP 会话。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import (
    __version__,
    command,
    coordinator,
    hook,
    instructions,
    memory,
    session,
    subagent,
    worktree,
)
from . import mcp as mcp_client
from . import skills as skills_pkg
from . import task as task_pkg
from .agent.agent_tool import AgentTool
from .agent.runtime import SessionRuntime
from .command.context import hook_base_payload  # ch12
from .command.skills import register_skills_as_commands
from .compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from .config import ConfigError, effective_context_window, load
from .hook import Event  # ch12
from .llm import new_provider
from .permission import new_engine
from .tool import new_default_registry
from .tool.install_skill import InstallSkillTool
from .tool.load_skill import LoadSkillTool
from .tui.run import run


def _setup_logging(root: str) -> None:
    """把运行时日志重定向到 `.novacode/logs/novacode.log`。

    背景：TUI 是 prompt_toolkit 非全屏渲染，任何 stderr 输出（记忆更新失败等
    warning/exception 经 logging 默认打到 stderr）都会落在输入框区域、破坏渲染
    （输入框被日志文字占据、光标错位、残留多余横线）。日志改走文件后终端干净。
    root logger 一旦有 handler，logging 的 lastResort（stderr）便不再触发。
    """
    try:
        log_dir = Path(root) / ".novacode" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "novacode.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    except OSError as e:
        # 日志目录创建失败不阻断启动（仅提示，日志不落盘）
        print(f"日志初始化失败（继续运行，日志不落盘）: {e}", file=sys.stderr)


async def _amain(args: argparse.Namespace) -> int:
    """加载配置并启动 NovaCode；配置错误时打印可读信息并以非零码退出。

    ch15：`--team-member` 时先 chdir 到 worktree（Pane 子进程的 cwd 与沙箱根都指到 worktree），
    wire 完依赖后直接跑 `run_team_member`，不构造 TUI（F19a）。
    """
    if args.team_member and args.worktree:
        os.chdir(args.worktree)
    try:
        cfg = load(".novacode/config.yaml")
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 1
    registry = new_default_registry()
    # ch10：slash 命令注册中心——注册期冲突（重名/别名）在此抛 RuntimeError 终止启动（F2）
    cmd_registry = command.Registry()
    command.register_builtins(cmd_registry)
    root = str(Path.cwd().resolve())
    _setup_logging(root)  # 日志走文件，避免污染非全屏 TUI
    # ch09：第 1 层项目指令 + 第 4 层记忆索引（provider 未选定，先空，选定后 set_provider）
    instruction_text = instructions.Loader(root, user_home=str(Path.home())).load()
    mem_mgr = memory.Manager(
        str(Path(root) / ".novacode" / "memory"),
        str(Path.home() / ".novacode" / "memory"),
        provider=None,
        model="",
    )
    memory_text = mem_mgr.load_index()
    mcp_cfg = mcp_client.load_config(root)  # 两层 YAML + ${VAR} 展开 + 字段校验
    mgr = await mcp_client.new_manager(mcp_cfg, version=__version__)
    clean_task: asyncio.Task | None = None
    try:
        for t in mgr.tools():
            registry.register(t)  # MCP 工具与 6 个内置工具并存注册进同一工具中心
        engine, err = new_engine(root)
        if err is not None:
            # 引擎必非 None（new_engine 致命错也返回空规则安全引擎），降级提示后继续
            print(f"权限引擎降级: {err}", file=sys.stderr)
        # ch12：Hook 引擎加载（配置错误内部已 stderr 提示，不阻断启动，N1/N9）
        hook_engine = hook.load(root)
        # ch13：SubAgent 角色目录 + 后台任务管理 + 5 个新工具（Agent + 4 个 task 元工具）
        subagent_catalog = subagent.load_catalog(root)
        task_mgr = task_pkg.Manager()
        # ch14：Worktree 管理器（非 git 仓库等场景降级为 None，不阻断启动；后台跑过期清理）
        try:
            worktree_mgr = worktree.Manager(root)
        except Exception as exc:  # noqa: BLE001 降级提示后继续
            print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
            worktree_mgr = None
        else:
            assert worktree_mgr is not None  # try 成功分支必非 None
            asyncio.create_task(
                worktree_mgr.sweep_stale(datetime.now().astimezone() - timedelta(hours=24))
            )
        registry.register(task_pkg.TaskListTool(task_mgr))
        registry.register(task_pkg.TaskGetTool(task_mgr))
        registry.register(task_pkg.TaskStopTool(task_mgr))
        registry.register(task_pkg.SendMessageTool(task_mgr))
        # ch15：Team 管理器 + 7 个新工具 + AgentTool.team_hook + on_task_done
        from .team import Manager as TeamManager
        from .team.registry import AgentNameRegistry
        from .team.tools import (
            TeamCreateTool,
            TeamDeleteTool,
            TeamSendMessageTool,
            TeamTaskCreateTool,
            TeamTaskGetTool,
            TeamTaskListTool,
            TeamTaskUpdateTool,
        )

        name_reg = AgentNameRegistry()
        task_mgr.set_name_registry(name_reg)
        try:
            team_mgr = TeamManager(str(Path.home()), worktree_mgr, task_mgr, name_reg)
        except Exception as exc:  # noqa: BLE001 降级提示后继续
            print(f"Team 管理器降级: {exc}", file=sys.stderr)
            team_mgr = None
        if team_mgr is not None:
            registry.register(TeamCreateTool(team_mgr))
            registry.register(TeamDeleteTool(team_mgr))
            registry.register(TeamTaskCreateTool(team_mgr))
            registry.register(TeamTaskGetTool(team_mgr))
            registry.register(TeamTaskListTool(team_mgr))
            registry.register(TeamTaskUpdateTool(team_mgr))
            registry.register(TeamSendMessageTool(team_mgr))

            async def _on_task_done(task_id: str) -> None:
                await team_mgr.handle_task_done(task_id)

            task_mgr.on_task_done(_on_task_done)  # ch15 F45：队员 idle 通知
        registry.register(
            AgentTool(
                subagent_catalog,
                task_mgr,
                parent=None,  # 主 Agent 在 tui.run 内构造后 set_parent 回填
                bg_enabled=cfg.effective_enable_subagent_background(),
                worktree_mgr=worktree_mgr,  # ch14
                team_hook=team_mgr,  # ch15：Team spawn 委托
                fork_teammate=cfg.features.fork_teammate,  # ch15
            )
        )
        # ch11：Skill Catalog 三层加载 + ActiveSkills（挂到 runtime）
        catalog = skills_pkg.Catalog.load(Path(root))
        active_skills = skills_pkg.ActiveSkills()
        runtime = SessionRuntime(  # ch08：跨 run 的上下文管理状态
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=new_session_context(root),
            context_window=200000,  # 多 provider 时由 tui.run 在选定后覆盖
            active_skills=active_skills,  # ch11 F24
        )
        # ch11：注册 LoadSkill（系统工具）与 InstallSkill（普通工具）
        registry.register(LoadSkillTool(catalog, active_skills, registry))
        registry.register(InstallSkillTool(catalog, Path(root)))
        # ch11 F15：fail-fast 依赖检查——引用的工具未注册则剔除该 Skill
        issues = catalog.validate_tools(registry)
        for issue in issues:
            print(
                f"[skill] error: skill {issue.skill_name}: allowed_tool "
                f'"{issue.tool_name}" not registered, skipped',
                file=sys.stderr,
            )
            catalog.remove(issue.skill_name)
        # ch11 F18：Skill 自动注册为 Slash Command（内置 /review 已删、/skill 已加）
        register_skills_as_commands(cmd_registry, catalog)
        # ch09：第 2 层 JSONL 会话写入器 + 后台过期清理（30 天，F25/F26）
        writer = session.Writer(runtime.session.session_dir)
        sessions_dir = str(Path(root) / ".novacode" / "sessions")
        clean_task = asyncio.create_task(
            asyncio.to_thread(session.clean_expired, sessions_dir, timedelta(days=30))
        )
        # ch15：Pane 后端子进程——不构造 TUI，跑 run_team_member 自治循环（F19a）
        if args.team_member:
            from .team.member import run_team_member

            provider_cfg = cfg.providers[0]
            provider = new_provider(provider_cfg)
            runtime.context_window = effective_context_window(provider_cfg)
            return await run_team_member(
                team_mgr,
                provider,
                registry,
                engine,
                runtime,
                subagent_catalog,
                hook_engine,
                args,
            )
        coord_enabled = coordinator.is_enabled(cfg)
        code = await run(
            cfg,
            registry,
            engine,
            runtime,
            cmd_registry=cmd_registry,
            writer=writer,
            mem_mgr=mem_mgr,
            instruction_text=instruction_text,
            memory_text=memory_text,
            sessions_dir=sessions_dir,
            catalog=catalog,  # ch11
            hook_engine=hook_engine,  # ch12
            task_mgr=task_mgr,  # ch13
            subagent_catalog=subagent_catalog,  # ch13
            worktree_mgr=worktree_mgr,  # ch14
            team_mgr=team_mgr,  # ch15
            coordinator_mode=coord_enabled,  # ch15
            coordinator_allowed=coordinator.allowed_tools() if coord_enabled else None,  # ch15
            coordinator_prompt=coordinator.system_prompt_suffix() if coord_enabled else "",  # ch15
        )
        # ch12：进程退出兜底 SessionEnd（/exit 与 Ctrl+C 都走这里；/clear、/resume 由命令 handler 内部 emit）
        if hook_engine is not None:
            await hook_engine.dispatch(
                Event.SESSION_END,
                hook_base_payload(
                    runtime=runtime, root=root, mode=engine.start_mode, event=Event.SESSION_END
                ),
            )
        return code
    finally:
        # ch09 R12：退出收尾——等待后台清理与未完成记忆更新 task，再关 MCP
        if clean_task is not None:
            await asyncio.gather(clean_task, return_exceptions=True)
        await mem_mgr.wait_pending()
        await mgr.close()  # 退出时统一关闭全部 MCP 会话（stdio 子进程终止 / HTTP 断开）


def main() -> None:
    """CLI 入口：解析参数后 `asyncio.run(_amain(args))`；Ctrl+C 作为最后兜底、以 0 退出。

    ch15：`--team-member` 及相关 flags 供 Pane 后端 spawn 子进程用（`build_member_cmd`）。
    """
    parser = argparse.ArgumentParser(prog="novacode", description="NovaCode 终端 AI 编程助手")
    parser.add_argument("--team-member", action="store_true", help="Pane 后端队员自治模式（ch15）")
    parser.add_argument("--team", default="", help="队员所属 Team")
    parser.add_argument("--member", default="", help="队员名")
    parser.add_argument("--agent-id", default="", help="队员 agent_id")
    parser.add_argument("--session-dir", default="", help="队员 session 目录")
    parser.add_argument("--worktree", default="", help="队员 worktree 路径")
    parser.add_argument("--agent-type", default="", help="角色定义名")
    parser.add_argument("--model", default="", help="模型覆盖（本期不切 provider）")
    parser.add_argument(
        "--plan-mode", action="store_true", help="以 plan 模式起步（plan_mode_required）"
    )
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_amain(args)))
    except KeyboardInterrupt:
        pass  # prompt_toolkit 已还原终端；Ctrl+C 作为最后兜底
    except Exception as e:  # noqa: BLE001 启动兜底：可读错误而非崩溃堆栈（N4）
        print(f"启动失败: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
