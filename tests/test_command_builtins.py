"""内置命令注册与 handler 行为单测（ch10 T8）。"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from novacode.command import CommandContext, Registry, register_builtins
from novacode.command.builtin_local import (
    handle_memory,
    handle_permission,
    handle_session,
    handle_status,
)
from novacode.command.builtin_prompt import handle_do
from novacode.command.builtin_ui import handle_clear, handle_compact, handle_exit, handle_plan
from novacode.command.builtin_worktree import handle_worktree  # ch14
from novacode.permission import Mode

ALL_NAMES = [
    "clear",
    "compact",
    "do",
    "exit",
    "help",
    "hooks",  # ch12
    "memory",
    "permission",
    "plan",
    "resume",
    "session",
    "skill",  # ch11：/review 内置移除、加 /skill
    "status",
    "worktree",  # ch14：带参数命令（args_handler）
    "team",  # ch15：/team 系列（Kind.LOCAL + args_handler）
]


def _console() -> Console:
    return Console(file=io.StringIO(), highlight=False, width=80)


class _ConvRec:
    """记录 add_user 的假 conversation（CommandContext.conv 读 holder.conv）。"""

    def __init__(self) -> None:
        self.added: list[str] = []

    def add_user(self, text: str) -> None:
        self.added.append(text)


class _UsageBox:
    def __init__(self) -> None:
        self.usage_in = 10
        self.usage_out = 20


class _MemMgr:
    def list_files(self):
        return (["MEMORY.md", "a.md"], ["b.md"])


class _FakeAgent:
    async def run_force_compact(self, conv, defs):
        return 100, 50


def _ctx(**over) -> CommandContext:
    """构造一个字段齐全的 CommandContext 测试桩；over 用 setattr 覆盖。"""
    calls: list[bool] = []

    async def _cb() -> None:
        calls.append(True)

    ctx = CommandContext(
        holder=SimpleNamespace(conv=_ConvRec(), writer=None, ses_ctx=None),
        registry=SimpleNamespace(
            count=lambda: 6,
            read_only_definitions=list,
            definitions=list,
        ),
        mode_box=SimpleNamespace(mode=Mode.DEFAULT),
        agent=_FakeAgent(),
        console=_console(),
        exit_flag=[False],
        run_callback=_cb,
        provider=SimpleNamespace(model="fake-model"),
        usage_box=_UsageBox(),
        mem_mgr=_MemMgr(),
        runtime=SimpleNamespace(session=SimpleNamespace(session_id="s1")),
        root="/tmp/proj",
        sessions_dir="/tmp/proj/.novacode/sessions",
    )
    for k, v in over.items():
        setattr(ctx, k, v)
    ctx._calls = calls
    return ctx


class _ClearRuntime:
    """带 reset 记录的假 SessionRuntime（/clear 用）。"""

    def __init__(self, ses_ctx) -> None:
        self.session = ses_ctx
        self.reset_called = False

    def reset_for_new_session(self, ses_ctx) -> None:
        self.session = ses_ctx
        self.reset_called = True


def _clear_ctx(tmp_path) -> CommandContext:
    """/clear 专用的真实 writer/runtime ctx（副作用在 tmp_path 内）。"""
    from novacode.compact import new_session_context
    from novacode.session import Writer

    ses = new_session_context(str(tmp_path))
    writer = Writer(ses.session_dir)
    writer.set_model("m")
    holder = SimpleNamespace(conv=_ConvRec(), writer=writer, ses_ctx=ses)
    return _ctx(holder=holder, runtime=_ClearRuntime(ses), root=str(tmp_path))


def _registered() -> Registry:
    reg = Registry()
    register_builtins(reg)
    return reg


# ─────────────────────────── 注册完整性 ───────────────────────────


def test_register_builtins_all_registered():
    reg = _registered()
    assert len(reg.visible()) == len(ALL_NAMES)
    for name in ALL_NAMES:
        assert reg.lookup(name) is not None
    assert [c.name for c in reg.visible()] == sorted(ALL_NAMES)


def test_register_builtins_no_collision():
    _registered()  # 不抛即通过


@pytest.mark.asyncio
async def test_all_handlers_run_on_fake_ctx(tmp_path):
    """全部命令的 handler（或 args_handler）都能在假 ctx 上正常 await，不抛。"""
    reg = _registered()
    for cmd in reg.visible():
        ctx = _clear_ctx(tmp_path) if cmd.name == "clear" else _ctx()
        if cmd.args_handler is not None:  # ch14：带参数命令走 args_handler
            await cmd.args_handler(ctx, "")
        else:
            await cmd.handler(ctx)  # type: ignore[misc]  # CommandContext 字段齐全


# ─────────────────────────── 行为断言 ───────────────────────────


@pytest.mark.asyncio
async def test_handle_status_prints_all_keys():
    ctx = _ctx()
    await handle_status(ctx)
    out = ctx.console.file.getvalue()
    for key in ("Mode:", "Tokens:", "Tools:", "Memories:", "Model:", "Directory:"):
        assert key in out
    assert "fake-model" in out
    assert "/tmp/proj" in out
    assert "10 in / 20 out" in out
    assert "6 enabled" in out
    assert "3 files" in out  # 2 项目层 + 1 用户层


@pytest.mark.asyncio
async def test_handle_memory_lists_filenames():
    ctx = _ctx()
    await handle_memory(ctx)
    out = ctx.console.file.getvalue()
    assert "MEMORY.md" in out and "a.md" in out and "b.md" in out


@pytest.mark.asyncio
async def test_handle_memory_empty_hint():
    ctx = _ctx(mem_mgr=None)
    await handle_memory(ctx)
    assert "无已加载的记忆文件" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_handle_permission_prints_mode_name():
    ctx = _ctx()
    await handle_permission(ctx)
    # IntEnum 的 value 是整数，展示用 __str__（default/plan/acceptEdits/...）
    assert ctx.console.file.getvalue().strip() == str(ctx.mode_box.mode)


@pytest.mark.asyncio
async def test_handle_session_prints_id_and_path(tmp_path):
    ctx = _ctx()
    await handle_session(ctx)
    out = ctx.console.file.getvalue()
    assert "Session: s1" in out
    assert "Path: " in out


@pytest.mark.asyncio
async def test_handle_compact_runs_force_compact():
    ctx = _ctx()
    await handle_compact(ctx)
    assert "已压缩" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_handle_compact_plan_mode_uses_readonly():
    """PLAN 模式下用只读工具定义。"""
    seen: list[str] = []

    class _Agent:
        async def run_force_compact(self, conv, defs):
            seen.append("readonly" if defs == "RO" else "all")
            return 100, 50

    agent = _Agent()
    ctx = _ctx(mode_box=SimpleNamespace(mode=Mode.PLAN), agent=agent)
    ctx.registry.read_only_definitions = lambda: "RO"  # type: ignore[attr-defined]
    ctx.registry.definitions = lambda: "ALL"  # type: ignore[attr-defined]
    await handle_compact(ctx)
    assert seen == ["readonly"]


@pytest.mark.asyncio
async def test_handle_do_sets_mode_and_injects():
    from novacode.prompt import EXECUTE_DIRECTIVE

    ctx = _ctx(mode_box=SimpleNamespace(mode=Mode.PLAN))
    await handle_do(ctx)
    assert ctx.mode_box.mode == Mode.DEFAULT
    assert ctx.holder.conv.added == [EXECUTE_DIRECTIVE]
    assert ctx._calls  # run_callback 被触发


@pytest.mark.asyncio
async def test_handle_exit_sets_flag():

    ctx = _ctx()
    await handle_exit(ctx)
    assert ctx.exit_flag[0] is True


@pytest.mark.asyncio
async def test_handle_plan_sets_plan_mode():

    ctx = _ctx()
    await handle_plan(ctx)
    assert ctx.mode_box.mode == Mode.PLAN
    assert "计划模式" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_handle_clear_resets_usage_and_switches(tmp_path):
    """/clear 后 usage 归零、holder.writer/conv 替换、runtime.session 替换、旧会话保留（N9）。"""
    ctx = _clear_ctx(tmp_path)
    old_ses = ctx.runtime.session
    old_writer = ctx.holder.writer
    old_conv = ctx.holder.conv
    await handle_clear(ctx)

    assert ctx.usage_box.usage_in == 0 and ctx.usage_box.usage_out == 0
    assert ctx.holder.writer is not old_writer  # writer 已替换
    assert ctx.holder.conv is not old_conv  # conversation 已重建
    assert ctx.runtime.session.session_id != old_ses.session_id
    assert ctx.runtime.reset_called
    assert ctx.holder.ses_ctx.session_id == ctx.runtime.session.session_id
    out = ctx.console.file.getvalue()
    assert "已结束当前会话" in out and "开启新会话" in out
    # 旧会话 JSONL 保留，/resume 可见（N9）
    assert (
        tmp_path / ".novacode" / "sessions" / old_ses.session_id / "conversation.jsonl"
    ).is_file()


# ─────────────────────────── ch14 /worktree ───────────────────────────


class _FakeWtMgr:
    """/worktree handler 用的 stub Manager（duck typing）。"""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.entered: list[str] = []
        self.exited: list[tuple] = []
        self.removed: list[str] = []
        self.current_session = None
        self._list: list = []

    async def create(self, slug, base_ref, manual):
        self.created.append(slug)
        return SimpleNamespace(path=f"/wt/{slug}", branch=f"worktree-{slug}")

    async def enter(self, slug):
        self.entered.append(slug)
        return SimpleNamespace(worktree_path=f"/wt/{slug}")

    async def exit(self, name, action, opts):
        self.exited.append((name, str(action.value), opts.discard_changes))
        return SimpleNamespace(path=f"/wt/{name}", branch=f"worktree-{name}")

    async def remove(self, slug, opts):
        self.removed.append(slug)

    def list(self):
        return self._list


def _wt_ctx(mgr, set_cwd=None) -> CommandContext:
    return _ctx(worktree_mgr=mgr, set_active_cwd=set_cwd)


@pytest.mark.asyncio
async def test_worktree_registered():
    """ch14：/worktree 注册为带参命令（args_handler 非空，handler 为 None）。"""
    reg = _registered()
    cmd = reg.lookup("worktree")
    assert cmd is not None
    assert cmd.args_handler is not None
    assert cmd.handler is None


@pytest.mark.asyncio
async def test_handle_worktree_create():
    mgr = _FakeWtMgr()
    ctx = _wt_ctx(mgr)
    await handle_worktree(ctx, "create demo")
    assert mgr.created == ["demo"]
    out = ctx.console.file.getvalue()
    assert "Worktree 已创建" in out
    assert "/wt/demo" in out
    assert "worktree-demo" in out


@pytest.mark.asyncio
async def test_handle_worktree_enter_sets_active_cwd():
    mgr = _FakeWtMgr()
    set_cwd: list[str] = []
    ctx = _wt_ctx(mgr, set_cwd.append)
    await handle_worktree(ctx, "enter demo")
    assert set_cwd == ["/wt/demo"]
    assert "已进入 demo" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_handle_worktree_exit_remove_clears_active_cwd():
    mgr = _FakeWtMgr()
    mgr.current_session = SimpleNamespace(worktree_name="demo", worktree_path="/wt/demo")
    set_cwd: list[str] = []
    ctx = _wt_ctx(mgr, set_cwd.append)
    await handle_worktree(ctx, "exit --remove")
    assert mgr.exited == [("demo", "remove", False)]
    assert set_cwd == [""]  # 退出后清空 active_cwd 回进程 cwd
    assert "已退出并删除" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_handle_worktree_remove():
    mgr = _FakeWtMgr()
    ctx = _wt_ctx(mgr)
    await handle_worktree(ctx, "remove demo --discard")
    assert mgr.removed == ["demo"]
    assert "已删除 Worktree" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_handle_worktree_unconfigured():
    ctx = _wt_ctx(None)
    await handle_worktree(ctx, "list")
    assert "Worktree 未启用" in ctx.console.file.getvalue()


@pytest.mark.asyncio
async def test_handle_worktree_unknown_subcommand():
    ctx = _wt_ctx(_FakeWtMgr())
    await handle_worktree(ctx, "bogus")
    assert "未知子命令" in ctx.console.file.getvalue()
