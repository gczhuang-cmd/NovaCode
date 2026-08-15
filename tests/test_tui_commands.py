"""TUI 命令分发层单测（ch10 T13）：dispatch_slash 的 parse/lookup/handler 流程。

覆盖大小写不敏感、未知命令引导、非命令输入放行、handler 异常兜底、
/plan 不触发 LLM、/do 注入并触发回合。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from novacode.command import Command, CommandContext, Kind, Registry, register_builtins
from novacode.command.builtin_hooks import handle_hooks
from novacode.permission import Mode
from novacode.prompt import EXECUTE_DIRECTIVE
from novacode.tui.run import dispatch_slash


def _console() -> Console:
    return Console(file=io.StringIO(), highlight=False, width=80)


class _ConvRec:
    """记录 add_user 的假 conversation。"""

    def __init__(self) -> None:
        self.added: list[str] = []

    def add_user(self, text: str) -> None:
        self.added.append(text)


def _ctx(console: Console | None = None, **over) -> CommandContext:
    """构造一个可跑 dispatch_slash 的 CommandContext 桩。"""
    calls: list[bool] = []

    async def _cb() -> None:
        calls.append(True)

    ctx = CommandContext(
        holder=SimpleNamespace(conv=_ConvRec(), writer=None, ses_ctx=None),
        registry=SimpleNamespace(),
        mode_box=SimpleNamespace(mode=Mode.DEFAULT),
        agent=None,
        console=console or _console(),
        exit_flag=[False],
        run_callback=_cb,
        provider=None,
        usage_box=SimpleNamespace(usage_in=0, usage_out=0),
        mem_mgr=None,
        runtime=SimpleNamespace(session=SimpleNamespace(session_id="s1")),
        root="/tmp/proj",
        sessions_dir="/tmp/proj/.novacode/sessions",
    )
    for k, v in over.items():
        setattr(ctx, k, v)
    ctx._calls = calls
    return ctx


def _reg() -> Registry:
    reg = Registry()
    register_builtins(reg)
    return reg


async def _raise_handler(ctx):
    raise RuntimeError("boom-命令")


# ─────────────────────────── 分发流程 ───────────────────────────


@pytest.mark.asyncio
async def test_dispatch_not_slash_returns_false():
    ctx = _ctx()
    assert await dispatch_slash(ctx, _reg(), "hello") is False
    assert await dispatch_slash(ctx, _reg(), "") is False
    assert ctx.console.file.getvalue() == ""


@pytest.mark.asyncio
async def test_dispatch_case_insensitive():
    """F4：/Help 与 /help 同效。"""
    ctx = _ctx()
    reg = _reg()
    assert await dispatch_slash(ctx, reg, "/Help") is True
    assert "help" in ctx.console.file.getvalue()
    assert "status" in ctx.console.file.getvalue()  # help 输出列出全部命令


@pytest.mark.asyncio
async def test_dispatch_unknown_friendly():
    """F6：未命中命令 → 引导 /help，不触发 add_user。"""
    ctx = _ctx()
    assert await dispatch_slash(ctx, _reg(), "/foobar") is True
    out = ctx.console.file.getvalue()
    assert "未知命令" in out and "/help" in out
    assert ctx.holder.conv.added == []


@pytest.mark.asyncio
async def test_dispatch_slash_only_unknown():
    """纯 "/"（退化输入）→ 未知命令提示，且不拼出悬空斜杠。"""
    ctx = _ctx()
    assert await dispatch_slash(ctx, _reg(), "/") is True
    out = ctx.console.file.getvalue()
    assert "未知命令" in out and "输入 /help" in out
    assert "/," not in out  # 不出现 "未知命令: /, ..."


@pytest.mark.asyncio
async def test_dispatch_handler_exception_shows_error_block():
    """handler 抛异常 → error_block 文案（含 ERROR 语义），返回 True。"""
    reg = Registry()
    reg.register(Command(name="boom", description="抛错", kind=Kind.LOCAL, handler=_raise_handler))
    ctx = _ctx()
    assert await dispatch_slash(ctx, reg, "/boom") is True
    assert "boom-命令" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_dispatch_plan_no_llm():
    """F10/N3a：/plan 不调 conv.add_user、不触发回合，只改模式。"""
    ctx = _ctx()
    assert await dispatch_slash(ctx, _reg(), "/plan") is True
    assert ctx.mode_box.mode == Mode.PLAN
    assert ctx.holder.conv.added == []
    assert ctx._calls == []


@pytest.mark.asyncio
async def test_dispatch_do_injects_and_sends():
    """F11/N3：/do 注入 EXECUTE_DIRECTIVE 并触发回合。"""
    ctx = _ctx(mode_box=SimpleNamespace(mode=Mode.PLAN))
    assert await dispatch_slash(ctx, _reg(), "/do") is True
    assert ctx.mode_box.mode == Mode.DEFAULT
    assert ctx.holder.conv.added == [EXECUTE_DIRECTIVE]
    assert ctx._calls == [True]


@pytest.mark.asyncio
async def test_dispatch_exit_sets_flag():
    """/exit 置 exit_flag（主循环据此 break，ch10 bug 修复）。"""
    ctx = _ctx()
    assert await dispatch_slash(ctx, _reg(), "/exit") is True
    assert ctx.exit_flag[0] is True
    assert ctx.holder.conv.added == []


@pytest.mark.asyncio
async def test_dispatch_help_lists_all_builtins():
    """AC1：/help 输出含全部 12 个命令名。"""
    ctx = _ctx()
    assert await dispatch_slash(ctx, _reg(), "/help") is True
    out = ctx.console.file.getvalue()
    for name in (
        "clear",
        "compact",
        "do",
        "exit",
        "help",
        "memory",
        "permission",
        "plan",
        "resume",
        "session",
        "skill",
        "status",
    ):
        assert f"/{name}" in out


# ── ch12 /hooks 命令 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hooks_command_lists():
    """/hooks 按 event 分组、每条一行 + [once]/[async] 标志 + 加载来源（F34）。"""
    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine
    from novacode.hook.rule import Action, ActionType, PromptAction, Rule, ShellAction

    hook_engine = HookEngine(
        [
            Rule(
                name="zh-cn",
                event=HookEvent.SESSION_START,
                action=Action(type=ActionType.PROMPT, prompt=PromptAction(text="用 zh-CN")),
            ),
            Rule(
                name="block",
                event=HookEvent.PRE_TOOL_USE,
                only_once=True,
                action=Action(type=ActionType.SHELL, shell=ShellAction(command="exit 2")),
            ),
            Rule(
                name="notify",
                event=HookEvent.STOP,
                asyncio_mode=True,
                action=Action(type=ActionType.SHELL, shell=ShellAction(command="echo x")),
            ),
        ],
        ["proj.yaml", "user.yaml"],
    )
    ctx = _ctx(hook_engine=hook_engine)
    await handle_hooks(ctx)
    out = ctx.console.file.getvalue()
    assert "SessionStart:" in out
    assert "  zh-cn  SessionStart  prompt" in out
    assert "  block  PreToolUse  shell [once]" in out
    assert "  notify  Stop  shell [async]" in out
    assert "Loaded from: proj.yaml, user.yaml" in out


@pytest.mark.asyncio
async def test_hooks_command_empty():
    """无 hook 时输出 No hooks loaded.（F35）。"""
    ctx = _ctx(hook_engine=None)
    await handle_hooks(ctx)
    assert "No hooks loaded." in ctx.console.file.getvalue()
