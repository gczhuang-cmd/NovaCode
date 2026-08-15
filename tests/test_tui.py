"""TUI 层测试（scrollback + 每轮输入框架构）：view 渲染、流式消费、增量 markdown、权限模式与待批准块。"""

import asyncio
import io
import json

from rich.console import Console

import novacode.tui.run as run_mod
from novacode.agent import Agent, ApprovalRequest
from novacode.agent.runtime import SessionRuntime
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall, ToolDelta, Usage
from novacode.permission import Mode, Outcome, new_engine
from novacode.tool import Registry, new_default_registry
from novacode.tui.run import (
    _dispatch_session_start,
    _dispatch_user_prompt_submit,
    _stream_reply,
    _StreamMarkdown,
    next_mode,
    outcome_for_index,
    status_text,
)
from novacode.tui.view import (
    approval_lines,
    assistant_markdown,
    error_block,
    tool_line,
    tool_result_summary,
    user_block,
)


def _engine(tmp_path):
    engine, err = new_engine(str(tmp_path))
    assert err is None
    return engine


class _ScriptProvider:
    """脚本化 provider：按 call_count 依次吐 scripts，超出重复最后一段。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, scripts: list) -> None:
        self.scripts = scripts
        self.call_count = 0

    async def stream(self, req: Request):
        idx = min(self.call_count, len(self.scripts) - 1)
        self.call_count += 1
        for ev in self.scripts[idx]:
            yield ev


class FakeProvider:
    """本地模拟流式输出的 provider。"""

    name = "fake"
    model = "fake-model"

    async def stream(self, req: Request):
        yield StreamEvent(text="你")
        yield StreamEvent(text="好")
        yield StreamEvent(done=True)


class ErrProvider:
    """本地模拟出错的 provider。"""

    name = "fake"
    model = "fake-model"

    async def stream(self, req: Request):
        yield StreamEvent(err=RuntimeError("401 鉴权失败（模拟）"))


class FakeLive:
    """模拟 Rich Live：记录 update，console 指向测试用 Console。"""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.updates: list[object] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, renderable, refresh: bool = False) -> None:
        self.updates.append(renderable)


def _console() -> Console:
    return Console(file=io.StringIO(), highlight=False, width=80)


def test_view_blocks():
    u = user_block("你好")
    assert "你好" in u.plain
    e = error_block(RuntimeError("boom"))
    assert "boom" in e.plain
    md = assistant_markdown("```python\nprint(1)\n```")
    assert "print(1)" in md.renderables[1].markup


def test_tool_line_and_summary():
    tl = tool_line("read_file", '{"path": "x"}')
    assert "read_file" in tl.plain and '{"path": "x"}' in tl.plain
    ok = tool_result_summary("成功内容", is_error=False)
    assert "成功内容" in ok.renderable.plain
    err = tool_result_summary("失败", is_error=True)
    assert "失败" in err.renderable.plain
    # 长结果 UI 截断
    long = tool_result_summary("\n".join(f"line{i}" for i in range(20)), is_error=False)
    assert "[truncated]" in long.renderable.plain


def test_stream_reply_appends_and_records_assistant(tmp_path):
    async def run():
        conv = Conversation()
        conv.add_user("hi")
        console = _console()
        agent = Agent(FakeProvider(), Registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console, Mode.DEFAULT, asyncio.Event())
        assert conv.messages()[-1].role == "assistant"
        assert conv.messages()[-1].content == "你好"
        assert "你好" in console.file.getvalue()

    asyncio.run(run())


def test_stream_reply_error_shows_message(tmp_path):
    async def run():
        conv = Conversation()
        conv.add_user("hi")
        console = _console()
        agent = Agent(ErrProvider(), Registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console, Mode.DEFAULT, asyncio.Event())
        assert "错误" in console.file.getvalue()
        assert all(m.role == "user" for m in conv.messages())  # assistant 未写入

    asyncio.run(run())


class ThinkingProvider:
    """本地模拟带思考增量、工具增量与 reasoning 用量的 provider。"""

    name = "fake"
    model = "fake-model"

    async def stream(self, req):
        yield StreamEvent(thinking="先算乘法")
        yield StreamEvent(thinking="再相加")
        yield StreamEvent(tool_delta=[ToolDelta(index=0, name="write_file", args='{"path":')])
        yield StreamEvent(text="正文完成")
        yield StreamEvent(usage=Usage(input_tokens=100, output_tokens=50, reasoning_tokens=30))
        yield StreamEvent(done=True)


def test_agent_forward_thinking_without_history(tmp_path):
    """思考/工具增量透传 Event，但 conversation 不写入思考（F6），usage 带 reasoning（F7）。"""

    async def run():
        conv = Conversation()
        conv.add_user("hi")
        agent = Agent(ThinkingProvider(), Registry(), "test", _engine(tmp_path))
        events = [ev async for ev in agent.run(conv, Mode.DEFAULT, asyncio.Event())]
        thinks = [ev.thinking for ev in events if ev.thinking]
        deltas = [d for ev in events if ev.tool_delta for d in ev.tool_delta]
        assert "".join(thinks) == "先算乘法再相加"
        assert deltas == [ToolDelta(index=0, name="write_file", args='{"path":')]
        # conversation 只含正文，不含思考（F6）
        assert conv.messages()[-1].role == "assistant"
        assert conv.messages()[-1].content == "正文完成"
        usage_ev = next(ev.usage for ev in events if ev.usage is not None)
        assert usage_ev.reasoning == 30

    asyncio.run(run())


class PhaseProvider:
    """按序吐：思考增量 → 工具参数增量 → 正文 → done（UX01 阶段切换）。"""

    name = "fake"
    model = "fake-model"

    async def stream(self, req):
        yield StreamEvent(thinking="思考第一行\n")
        yield StreamEvent(thinking="思考第二行\n")
        yield StreamEvent(tool_delta=[ToolDelta(index=0, name="write_file", args="abc")])
        yield StreamEvent(text="正文内容")
        yield StreamEvent(done=True)


def _renderable_plain(obj) -> str:
    """Rich renderable → 纯文本（Text 用 plain；Markdown/其他渲染到临时 console）。"""
    if hasattr(obj, "plain"):
        return obj.plain
    buf = io.StringIO()
    Console(file=buf, width=200).print(obj)
    return buf.getvalue()


def test_stream_reply_phase_transitions(tmp_path, monkeypatch):
    """思考 → 工具参数进度 → 正文 依次驱动 Live update（F1/F4/F3）。"""
    console = _console()
    fake_live = FakeLive(console)
    monkeypatch.setattr(run_mod, "Live", lambda *a, **k: fake_live)

    async def run():
        conv = Conversation()
        conv.add_user("hi")
        agent = Agent(PhaseProvider(), Registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console, Mode.DEFAULT, asyncio.Event())
        return conv

    conv = asyncio.run(run())
    plains = [_renderable_plain(u) for u in fake_live.updates]
    assert any("思考第一行" in p for p in plains)  # 推理段思考滚动
    assert any("正在生成 write_file 参数" in p for p in plains)  # 生成段工具进度
    assert any("正文内容" in p for p in plains)  # 正文打字机接管
    assert conv.messages()[-1].content == "正文内容"  # 思考不进对话（F6）


def test_stream_reply_ctrl_o_expand(tmp_path, monkeypatch):
    """Ctrl+O（expand 信号）切换思考展开：4 行思考展开后全文可见（滚动态只 3 行）。"""
    expand_ev = asyncio.Event()

    class _FakeWatcher:
        def __init__(self, cancel, expand=None) -> None:
            self.expand = expand_ev

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(run_mod, "_CancelWatcher", _FakeWatcher)

    class ExpandProvider:
        name = "fake"
        model = "fake-model"

        async def stream(self, req):
            yield StreamEvent(thinking="行一\n行二\n行三\n")
            expand_ev.set()  # 思考途中触发 Ctrl+O
            yield StreamEvent(thinking="行四\n")
            yield StreamEvent(done=True)

    console = _console()
    fake_live = FakeLive(console)
    monkeypatch.setattr(run_mod, "Live", lambda *a, **k: fake_live)

    async def run():
        conv = Conversation()
        conv.add_user("hi")
        agent = Agent(ExpandProvider(), Registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console, Mode.DEFAULT, asyncio.Event())

    asyncio.run(run())
    plains = [_renderable_plain(u) for u in fake_live.updates]
    # 展开态全文 4 行可见（滚动态只保留最近 3 行，不会出现行一开头）——证明 toggle 生效
    assert any(p.startswith("行一\n行二\n行三\n行四") for p in plains)


def test_stream_reply_completion_shows_reasoning_tokens(tmp_path):
    """完成统计行附本轮思考 tokens（F7）；无 reasoning 时不输出思考段。"""
    console = _console()

    async def run_with(provider):
        conv = Conversation()
        conv.add_user("hi")
        agent = Agent(provider, Registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console, Mode.DEFAULT, asyncio.Event())

    asyncio.run(run_with(ThinkingProvider()))  # usage 带 reasoning=30
    out = console.file.getvalue()
    assert "用时" in out
    assert "思考 30 tokens" in out

    console2 = _console()

    async def run_plain():
        conv = Conversation()
        conv.add_user("hi")
        agent = Agent(FakeProvider(), Registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console2, Mode.DEFAULT, asyncio.Event())

    asyncio.run(run_plain())
    assert "思考" not in console2.file.getvalue()
    assert "用时" in console2.file.getvalue()


def test_stream_markdown_commits_complete_line():
    """完整行立即提交进输出；未完成行留在 Live 尾部不提交。"""
    console = _console()
    md = _StreamMarkdown(FakeLive(console))
    md.feed("你好\n")
    assert "你好" in console.file.getvalue()
    # 未完成行：不进入提交输出
    md.feed("还在")
    out = console.file.getvalue()
    assert out.count("还在") == 0


def test_stream_markdown_code_block_syntax():
    """代码块逐行 Syntax 渲染；围栏未闭合前 flush 会收尾。"""
    console = _console()
    md = _StreamMarkdown(FakeLive(console))
    md.feed("```python\n")
    md.feed("def f(x):\n")
    md.feed("    return x\n")
    md.feed("```\n")
    out = console.file.getvalue()
    assert "def f(x):" in out
    assert "return x" in out


# ─────────────────────────── ch06 权限模式与待批准块 ───────────────────────────


def test_next_mode_cycles():
    """Shift+Tab 四档全循环：default→acceptEdits→plan→bypassPermissions→default。"""
    m = Mode.DEFAULT
    seen = []
    for _ in range(4):
        m = next_mode(m)
        seen.append(m)
    assert seen == [Mode.ACCEPT_EDITS, Mode.PLAN, Mode.BYPASS, Mode.DEFAULT]


def test_status_text_shows_mode_not_provider():
    """状态栏左侧常驻权限模式、不含 provider 名。"""
    t = str(status_text(Mode.DEFAULT, "deepseek-v4-flash", 1100, 136))
    assert "DEFAULT" in t
    assert "deepseek-v4-flash" in t
    assert "↑1.1k" in t and "↓136" in t
    assert "provider" not in t


def test_status_text_mode_labels():
    assert "ACCEPT EDITS" in str(status_text(Mode.ACCEPT_EDITS, "m", 0, 0))
    assert "PLAN" in str(status_text(Mode.PLAN, "m", 0, 0))
    assert "BYPASS" in str(status_text(Mode.BYPASS, "m", 0, 0))
    assert "DEFAULT" in str(status_text(Mode.DEFAULT, "m", 0, 0))


def test_outcome_for_index():
    assert outcome_for_index(0) == Outcome.ALLOW_ONCE
    assert outcome_for_index(1) == Outcome.ALLOW_FOREVER
    assert outcome_for_index(2) == Outcome.DENY_ONCE


def _req() -> ApprovalRequest:
    return ApprovalRequest(
        name="write_file",
        args='{"path": "x.txt"}',
        reason="default 模式下 文件写 类操作需确认",
        respond=None,  # 类型提示用；渲染不触碰 respond
    )


def test_approval_lines_render_and_cursor():
    lines = approval_lines(_req(), 0)
    text = "".join(t for _, t in lines)
    assert "write_file" in text
    assert "default 模式下" in text
    assert "1. 允许本次" in text and "2. 永久允许" in text and "3. 拒绝本次" in text
    assert "↑↓ 选择" in text
    # 光标 0：第一项高亮 `> `；每行以 \n 结尾（保证 ptk 分行渲染）
    assert lines[3] == ("bold green", "> 1. 允许本次\n")
    assert lines[4][1].startswith("  2.")
    # 光标 2：第三项高亮
    lines2 = approval_lines(_req(), 2)
    assert lines2[5] == ("bold green", "> 3. 拒绝本次\n")


def test_stream_reply_approval_allow_once(tmp_path, monkeypatch):
    """TUI 层收到 approval 事件 → 用户允许 → set_result → agent 续跑执行。"""
    write_call = ToolCall(
        id="c1",
        name="write_file",
        input=json.dumps({"path": str(tmp_path / "x.txt"), "content": "hi"}),
    )
    provider = _ScriptProvider(
        [
            [StreamEvent(tool_calls=[write_call]), StreamEvent(done=True)],
            [StreamEvent(text="写完了"), StreamEvent(done=True)],
        ]
    )

    async def fake_approval(console, req, cancel):
        return Outcome.ALLOW_ONCE

    monkeypatch.setattr(run_mod, "_run_approval", fake_approval)

    async def run():
        conv = Conversation()
        conv.add_user("写文件")
        console = _console()
        agent = Agent(provider, new_default_registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console, Mode.DEFAULT, asyncio.Event())
        assert (tmp_path / "x.txt").exists()  # 文件被写
        assert conv.messages()[-1].role == "assistant"

    asyncio.run(run())


def test_stream_reply_approval_cancel(tmp_path, monkeypatch):
    """待批准态取消 → cancel Event 置位 → 干净收尾、打印「已取消」、文件未写。"""
    write_call = ToolCall(
        id="c1",
        name="write_file",
        input=json.dumps({"path": str(tmp_path / "x.txt"), "content": "hi"}),
    )
    provider = _ScriptProvider([[StreamEvent(tool_calls=[write_call]), StreamEvent(done=True)]])

    async def fake_approval(console, req, cancel):
        cancel.set()

    monkeypatch.setattr(run_mod, "_run_approval", fake_approval)

    async def run():
        conv = Conversation()
        conv.add_user("写文件")
        console = _console()
        agent = Agent(provider, new_default_registry(), "test", _engine(tmp_path))
        await _stream_reply(agent, conv, console, Mode.DEFAULT, asyncio.Event())
        assert "已取消" in console.file.getvalue()
        assert not (tmp_path / "x.txt").exists()
        assert conv.messages()[-1].role == "assistant"  # 历史角色交替合法

    asyncio.run(run())


# ── ch12 Hook 接入 ──────────────────────────────────────────────────────────────


def _runtime(tmp_path) -> SessionRuntime:
    return SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(str(tmp_path)),
    )


def _hook_py(cmd_body: str) -> str:
    import sys

    return f'"{sys.executable}" -c "{cmd_body}"'


async def test_user_prompt_submit_blocked(tmp_path):
    """UserPromptSubmit 拦截：返回 (True, [hook <name>] <reason>)，调用方不写历史（F32）。"""
    import re

    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine
    from novacode.hook.rule import (
        Action,
        ActionType,
        AtomCondition,
        CombineMode,
        Condition,
        Rule,
        ShellAction,
    )
    from novacode.permission.matcher import RegexMatcher

    hook_engine = HookEngine(
        [
            Rule(
                name="warn-delete",
                event=HookEvent.USER_PROMPT_SUBMIT,
                condition=Condition(
                    mode=CombineMode.ALL_OF,
                    atoms=[
                        AtomCondition(
                            field="prompt",
                            matcher=RegexMatcher("(?i)delete", re.compile("(?i)delete")),
                        )
                    ],
                ),
                action=Action(
                    type=ActionType.SHELL,
                    shell=ShellAction(
                        command=_hook_py(
                            "import sys; print('no delete', file=sys.stderr); sys.exit(2)"
                        )
                    ),
                ),
            )
        ],
        ["t.yaml"],
    )
    runtime = _runtime(tmp_path)
    blocked, text = await _dispatch_user_prompt_submit(
        hook_engine, runtime, str(tmp_path), Mode.DEFAULT, "请帮我 delete 那个文件"
    )
    assert blocked
    assert text == "[hook warn-delete] no delete"
    # 非命中消息放行
    blocked2, _ = await _dispatch_user_prompt_submit(
        hook_engine, runtime, str(tmp_path), Mode.DEFAULT, "正常消息"
    )
    assert not blocked2


async def test_user_prompt_submit_injects_prompt(tmp_path):
    """UserPromptSubmit 放行时注入的 prompt 写入 runtime 队列（F33）。"""
    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine
    from novacode.hook.rule import Action, ActionType, PromptAction, Rule

    hook_engine = HookEngine(
        [
            Rule(
                name="safety",
                event=HookEvent.USER_PROMPT_SUBMIT,
                action=Action(type=ActionType.PROMPT, prompt=PromptAction(text="注意安全")),
            )
        ],
        ["t.yaml"],
    )
    runtime = _runtime(tmp_path)
    blocked, _ = await _dispatch_user_prompt_submit(
        hook_engine, runtime, str(tmp_path), Mode.DEFAULT, "hi"
    )
    assert not blocked
    assert runtime.take_reminders() == ["注意安全"]


async def test_dispatch_session_start_injects_prompt(tmp_path):
    """SessionStart 注入的 prompt 写入 runtime 队列（F6/AC6）。"""
    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine
    from novacode.hook.rule import Action, ActionType, PromptAction, Rule

    hook_engine = HookEngine(
        [
            Rule(
                name="zh-cn",
                event=HookEvent.SESSION_START,
                action=Action(type=ActionType.PROMPT, prompt=PromptAction(text="用 zh-CN 回复")),
            )
        ],
        ["t.yaml"],
    )
    runtime = _runtime(tmp_path)
    await _dispatch_session_start(hook_engine, runtime, str(tmp_path), Mode.DEFAULT)
    assert runtime.take_reminders() == ["用 zh-CN 回复"]
    # hook_engine 为 None 时不动作
    await _dispatch_session_start(None, runtime, str(tmp_path), Mode.DEFAULT)
    assert runtime.take_reminders() == []
