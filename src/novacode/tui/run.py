"""主循环（Claude Code / ForgeCode 风格）：Rich 输出直写终端 scrollback + 每轮轻量输入框。

分层（借鉴 ForgeCode 的做法）：
- 输出层 Rich：`console.print` 直接把对话输出打印进**终端 scrollback**，与命令行融为一体
  （终端滚轮/滚动条回看历史、随窗口自适应、永不截断、banner 只出现一次）。流式回复用
  `rich.live.Live` 做「打字机」：已完成的行经 `live.console.print` 提交进 scrollback，
  未完成的当前行在 Live 区域原地更新；`Markdown`/`Syntax`(pygments) 增量渲染。
- 输入层 prompt_toolkit：每轮一个「上横线 + ❯ 输入行 + 下横线 + 状态栏」的非全屏
  Application，输入框盒子尾随当前光标（窗口底部）。Enter 提交、Ctrl+J 换行、Ctrl+C 退出。
  提交后整框作为「用户回合」上滚进历史，回复打印其下，再在底部重绘新输入框。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

from .. import __version__
from .. import command as command_pkg
from .. import memory as mem_pkg
from .. import session as session_pkg
from .. import skills as skills_pkg
from ..agent import Agent, ApprovalRequest, Phase, ToolEvent
from ..agent.runtime import SessionRuntime
from ..command.context import hook_base_payload
from ..config import Config, effective_context_window
from ..conversation import Conversation
from ..hook import Event as HookEvent
from ..llm import new_provider
from ..permission import Engine, Mode, Outcome
from ..prompt import render_banner
from ..tool import Registry
from ..tool.ctx import with_cwd  # ch14：主 Agent Run 前注入 active_cwd（Worktree 隔离）
from .commands import format_compact_notice
from .completion import MAX_ROWS, CommandCompleter, _submit_selected, bind_completion_keys
from .leadmail import consume_lead_mail  # ch15：Lead 邮箱轮询
from .select import choose_provider
from .tasks import _inject_task_notifications  # ch13
from .thinking import _ThinkingView, _ToolProgress
from .view import approval_lines, error_block, tool_line, tool_result_summary

# 代码块背景色（随行着色，形成连续代码块观感）
_CODE_BG = "rgb(22,22,24)"

# ch15：输入框因 Lead 邮箱唤醒而退出的哨兵文本（主循环识别后走自动回合）
TEAM_AUTONOMOUS = "__TEAM_AUTONOMOUS__"


@dataclass
class _SessionHolder:
    """可变会话状态容器（ch09）：do_resume 恢复后替换内部引用，主循环读回新值。

    `conv` 经 CommandContext 的只读 property 读取；`writer` / `ses_ctx` 供后续
    写入与上下文管理使用。
    """

    conv: Conversation
    writer: session_pkg.Writer | None
    ses_ctx: object  # compact.SessionContext


@dataclass
class _UsageBox:
    """usage 可变容器（ch10）：/status 读取与 /clear 归零共用。

    替代原 run() 里的 nonlocal 变量，命令 handler 经 CommandContext 访问。
    """

    usage_in: int = 0
    usage_out: int = 0


# 代码块背景色（随行着色，形成连续代码块观感）
_CODE_BG = "rgb(22,22,24)"


class _StreamMarkdown:
    """把流式文本增量渲染进终端 scrollback（Live 打字机 + Markdown/pygments 高亮）。

    规则：
    - 已完成的行（含换行）经 `live.console.print` 提交进 scrollback（原生滚动）；
    - 未完成的当前行保留在 Live 区域原地更新，实现逐字打字机效果（Live 区域恒 ≤ 1 行，
      不溢出、不截断、不重复）；
    - 代码块逐行用 `Syntax`(pygments) 高亮；围栏开始/结束行以暗色标记打印。
    """

    def __init__(self, live: Live) -> None:
        self.live = live
        self.partial = ""  # 当前未完成行
        self._lang = ""  # 当前代码块语言；"" 表示不在代码块

    def feed(self, chunk: str) -> None:
        """喂入一段文本增量：提交已完成的行，Live 原地更新未完成行。"""
        self.partial += chunk
        while "\n" in self.partial:
            line, self.partial = self.partial.split("\n", 1)
            self._line(line)
        self.live.update(self._tail(), refresh=True)

    def flush(self) -> None:
        """回复结束：提交最后一行与收尾代码围栏，清空 Live 尾部。"""
        if self.partial:
            self._line(self.partial)
            self.partial = ""
        if self._lang:
            self._lang = ""
            self.live.console.print(Text(" ``` ", style="dim"))
        self.live.update(Text(""), refresh=True)

    def commit_partial(self) -> None:
        """提交当前未完成行（含未闭合代码块围栏），但不清空 Live 区域。

        供工具调用前提交 preamble 尾部，随后 Live 转为执行指示。
        """
        if self.partial:
            self._line(self.partial)
            self.partial = ""
        if self._lang:
            self._lang = ""
            self.live.console.print(Text(" ``` ", style="dim"))

    def _line(self, line: str) -> None:
        """处理一行完整文本。"""
        stripped = line.strip()
        if self._lang:  # 在代码块内
            if stripped.startswith("```"):
                self._lang = ""
                self.live.console.print(Text(f" {stripped} ", style="dim"))
            else:
                self.live.console.print(self._syntax(line))
            return
        if stripped.startswith("```"):
            self._lang = stripped[3:].strip().split()[0] if stripped[3:].strip() else "text"
            self.live.console.print(Text(f" {stripped} ", style="dim"))
            return
        if stripped:
            self.live.console.print(Markdown(line))

    def _syntax(self, line: str) -> object:
        """单行代码 → pygments 高亮；未知语言等异常降级为纯文本。"""
        try:
            return Syntax(line, self._lang, background_color=_CODE_BG)
        except Exception:  # noqa: BLE001 降级，不因高亮失败打断流式
            return Text(line, style=f"on {_CODE_BG}")

    def _tail(self) -> object:
        """Live 当前应显示的未完成尾部。"""
        if self._lang:
            return Syntax(self.partial + "\n", self._lang, background_color=_CODE_BG)
        return Markdown(self.partial)

    @property
    def has_partial(self) -> bool:
        """打字机是否正在显示未完成内容（含未闭合代码块）。"""
        return bool(self.partial) or bool(self._lang)


class _Phase(Enum):
    """Live 动态区当前阶段（UX01）：决定渲染什么。"""

    THINKING = "thinking"  # 推理段：思考内容滚动/展开
    GENERATING = "generating"  # 生成段：工具参数字符进度
    TEXT = "text"  # 正文：打字机
    TOOLS = "tools"  # 工具执行：Running 列表
    IDLE = "idle"  # 兜底：思考中计时


@dataclass
class _LiveState:
    """流式态共享渲染状态（ticker 与工具事件读写）。"""

    iter: int = 0  # 当前迭代轮次（进度显示）
    cur_tools: list[ToolEvent] = field(default_factory=list)  # 正在执行的工具（多工具并发）
    phase: _Phase = _Phase.IDLE  # UX01：Live 动态区阶段
    thinking_view: _ThinkingView = field(default_factory=_ThinkingView)
    tool_progress: _ToolProgress = field(default_factory=_ToolProgress)


async def _ticker(live: Live, start: float, state: _LiveState, renderer: _StreamMarkdown) -> None:
    """每秒更新 Live 的「思考中… (Ns · 第 N 轮)」计时（仅 IDLE 阶段；其余阶段由事件驱动渲染）。"""
    try:
        while True:
            if state.phase == _Phase.IDLE and not state.cur_tools and not renderer.has_partial:
                suffix = f" · 第 {state.iter} 轮" if state.iter > 0 else ""
                live.update(
                    Text(f"思考中… ({int(time.monotonic() - start)}s){suffix}", style="italic dim"),
                    refresh=True,
                )
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


def _update_dynamic(live: Live, state: _LiveState, renderer: _StreamMarkdown, start: float) -> None:
    """按当前阶段刷新动态区：思考滚动/展开 > 工具参数进度 > 正文打字机 > Running > 思考中计时。"""
    if state.phase == _Phase.THINKING:
        view = state.thinking_view.render()
        if view is None:
            live.update(Text("思考中…", style="italic dim"), refresh=True)
        else:
            live.update(view, refresh=True)
    elif state.phase == _Phase.GENERATING:
        prog = state.tool_progress.render()
        live.update(
            prog if prog is not None else Text("正在生成工具参数…", style="bold cyan"),
            refresh=True,
        )
    elif state.phase == _Phase.TEXT:
        if renderer.has_partial:
            live.update(renderer._tail(), refresh=True)
    elif state.phase == _Phase.TOOLS:
        lines = [Text(f"● {t.name}({t.args}) Running…", style="bold cyan") for t in state.cur_tools]
        live.update(Group(*lines), refresh=True)
    else:  # IDLE
        suffix = f" · 第 {state.iter} 轮" if state.iter > 0 else ""
        live.update(
            Text(f"思考中… ({int(time.monotonic() - start)}s){suffix}", style="italic dim"),
            refresh=True,
        )


def _fmt_tok(n: int) -> str:
    """紧凑 token 数值：≥1000 显示 `1.2k`。"""
    if n >= 1000:
        return f"{n / 1000:.1f}k".rstrip("0").rstrip(".")
    return str(n)


# 权限模式徽标（状态栏左侧常驻，取代 provider 名；AC9/F7）
_MODE_LABEL: dict[Mode, tuple[str, str]] = {
    Mode.DEFAULT: ("DEFAULT", "bold green"),
    Mode.ACCEPT_EDITS: ("ACCEPT EDITS", "bold cyan"),
    Mode.PLAN: ("PLAN", "bold yellow"),
    Mode.BYPASS: ("BYPASS", "bold red"),
}


def next_mode(m: Mode) -> Mode:
    """Shift+Tab 循环切换权限模式：default→acceptEdits→plan→bypassPermissions→default（四档全循环）。"""
    return Mode((int(m) + 1) % 4)


def outcome_for_index(i: int) -> Outcome:
    """待批准菜单光标索引 → Outcome（0=允许本次、1=永久、2=拒绝本次）。"""
    return (Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER, Outcome.DENY_ONCE)[i]


@dataclass
class _ModeBox:
    """可变模式容器：`_input_frame` 的 s-tab 回调经它更新，主循环读回（跨轮保持）。"""

    mode: Mode


def status_text(
    mode: Mode,
    model: str,
    usage_in: int,
    usage_out: int,
    coordinator_mode: bool = False,  # ch15：Coordinator 模式标签
) -> FormattedText:
    """状态栏：左侧常驻权限模式（取代 provider 名）+ 右侧模型名与累计 token 用量。"""
    label, style = _MODE_LABEL[mode]
    left = f" {label}"
    if coordinator_mode:
        left += " [COORDINATOR]"  # ch15 F54：Coordinator 模式标签
    right = ""
    if usage_in or usage_out:
        right += f"  ↑{_fmt_tok(usage_in)} ↓{_fmt_tok(usage_out)} tok"
    return FormattedText([(style, left), ("", f" · {model}{right}")])


class _CancelWatcher:
    """流式态按键监听：Esc / Ctrl+C → cancel.set()（方案1，当前架构无运行中的 ptk App）。

    - Linux：termios 半 raw（关 ICANON/ECHO/ISIG，保留 OPOST 使 `\n→\r\n` 正常），
      `loop.add_reader` 读 `\x1b` / `\x03`；
    - Windows：屏蔽 SIGINT 默认处理 + `msvcrt` 轮询 `getwch()`。
    start/stop 成对调用，确保恢复终端状态。
    """

    def __init__(self, cancel: asyncio.Event, expand: asyncio.Event | None = None) -> None:
        self._cancel = cancel
        self._expand = expand if expand is not None else asyncio.Event()  # Ctrl+O 展开思考
        self._fd: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._old_attr: list | None = None  # Linux termios 原始属性
        self._old_sigint: object = None  # Windows 原 SIGINT handler

    @property
    def expand(self) -> asyncio.Event:
        """Ctrl+O（0x0f）触发：TUI 据此切换思考展开态（非取消）。"""
        return self._expand

    def start(self) -> None:
        """启动监听；失败静默降级（仅 Esc 不可用，不影响对话）。"""
        if sys.platform == "win32":
            self._start_windows()
        else:
            self._start_linux()

    def stop(self) -> None:
        """停止监听并恢复终端状态。"""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._loop is not None and self._fd is not None:
            try:
                self._loop.remove_reader(self._fd)
            except (ValueError, OSError):
                pass
        self._restore_linux()
        self._fd = None
        if sys.platform == "win32" and self._old_sigint is not None:
            import signal

            signal.signal(signal.SIGINT, self._old_sigint)
            self._old_sigint = None

    def _start_linux(self) -> None:
        try:
            import termios
        except ImportError:
            return
        try:
            fd = sys.stdin.fileno()
            self._old_attr = termios.tcgetattr(fd)
            new = termios.tcgetattr(fd)
            # 半 raw：关 ICANON/ECHO/ISIG（Esc 与 Ctrl+C 都进输入缓冲），保留 OPOST
            new[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
            termios.tcsetattr(fd, termios.TCSANOW, new)
        except (OSError, ValueError, termios.error):
            self._old_attr = None
            return
        self._fd = fd
        self._loop = asyncio.get_running_loop()

        def _on_key() -> None:
            try:
                data = os.read(fd, 1)
            except OSError:
                return
            if data == b"\x0f":  # Ctrl+O：展开思考（非取消），可重复触发，不 teardown
                self._expand.set()
            elif data in (b"\x1b", b"\x03"):
                self._cancel.set()
                self._teardown_linux()

        self._loop.add_reader(fd, _on_key)

    def _teardown_linux(self) -> None:
        """按键命中后清理：移除 reader 并恢复终端。"""
        if self._loop is not None and self._fd is not None:
            try:
                self._loop.remove_reader(self._fd)
            except (ValueError, OSError):
                pass
        self._restore_linux()
        self._fd = None

    def _restore_linux(self) -> None:
        if self._fd is not None and self._old_attr is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSANOW, self._old_attr)
            except (OSError, ValueError, termios.error):
                pass
        self._old_attr = None

    def _start_windows(self) -> None:
        import msvcrt
        import signal

        # 屏蔽 Ctrl+C 默认 SIGINT，让 \x03 进入输入缓冲由 getwch 读到
        try:
            self._old_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except (ValueError, OSError):
            pass

        async def _watch() -> None:
            try:
                while not self._cancel.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch == "\x0f":  # Ctrl+O：展开思考（非取消），可重复触发，继续监听
                            self._expand.set()
                            continue
                        if ch in ("\x1b", "\x03"):
                            self._cancel.set()
                            break
                    await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                pass

        self._task = asyncio.create_task(_watch())


async def _run_approval(
    console: Console, req: ApprovalRequest, cancel: asyncio.Event
) -> Outcome | None:
    """待批准交互：非全屏输入框渲染待批准块 + 读键（↑↓·回车·1/2/3 直选·Esc 取消）。

    返回 Outcome（用户三选一）；用户取消时置 `cancel` Event 并返回 None，
    agent 的待批准等待据此走取消收尾（无需在 TUI 侧兜底 set_result）。
    """
    cursor = 0
    holder: dict[str, Outcome | None] = {}
    app_ref: dict[str, Application] = {}

    def _finish(outcome: Outcome | None) -> None:
        holder["outcome"] = outcome
        app_ref["app"].exit(result=outcome)

    def _move(delta: int) -> None:
        nonlocal cursor
        cursor = (cursor + delta) % 3
        app_ref["app"].invalidate()

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(_e) -> None:
        _move(-1)

    @kb.add("down")
    @kb.add("j")
    def _down(_e) -> None:
        _move(1)

    @kb.add("1")
    def _one(_e) -> None:
        _finish(outcome_for_index(0))

    @kb.add("2")
    def _two(_e) -> None:
        _finish(outcome_for_index(1))

    @kb.add("3")
    def _three(_e) -> None:
        _finish(outcome_for_index(2))

    @kb.add("enter")
    def _enter(_e) -> None:
        _finish(outcome_for_index(cursor))

    @kb.add("escape")
    def _esc(_e) -> None:
        cancel.set()
        _finish(None)

    @kb.add("c-c")
    def _cc(_e) -> None:
        cancel.set()
        _finish(None)

    @kb.add("<any>")
    def _ignore_other(_e) -> None:
        """忽略未绑定的普通字符（如误按的字母），防止被输入进 focus buffer。"""

    input_buf = Buffer()
    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(lambda: FormattedText(approval_lines(req, cursor))),
                    wrap_lines=True,
                    dont_extend_height=True,
                ),
                Window(
                    BufferControl(input_buf),
                    height=1,
                    dont_extend_height=True,
                ),
            ]
        ),
        focused_element=input_buf,
    )
    app = Application(layout=layout, full_screen=False, key_bindings=kb, mouse_support=False)
    app_ref["app"] = app
    await app.run_async()
    return holder.get("outcome")


async def _stream_reply(
    agent: Agent,
    conv: Conversation,
    console: Console,
    mode: Mode,
    cancel: asyncio.Event,
) -> tuple[int, int]:
    """驱动 Agent.run ReAct 循环，增量渲染进 scrollback。返回本轮累计 (usage_in, usage_out)。

    - 等待首个文本前：Live 显示「思考中… (Ns · 第 N 轮)」计时；
    - 文本增量：`_StreamMarkdown` 逐行提交进 scrollback，未完成行打字机更新；
    - 工具调用 START：提交 preamble 尾部 → 动态区显示「● name(args) Running…」（多工具多行）；
    - 工具调用 END：工具行 + 结果摘要顺序提交进 scrollback；
    - usage 累加、notice 灰字提示、iter 更新动态区轮次；
    - 结束：提交尾部并显示总耗时；出错/取消：可读提示（不退出）。
    """
    start = time.monotonic()
    state = _LiveState()
    live = Live(Text("思考中… (0s)", style="italic dim"), console=console, auto_refresh=False)
    renderer = _StreamMarkdown(live)
    watcher = _CancelWatcher(cancel)
    ticker = asyncio.create_task(_ticker(live, start, state, renderer))
    got_text = False
    usage_in = 0
    usage_out = 0
    reasoning_total = 0  # UX01 F7：本轮思考 token（真实 usage.reasoning）
    err_text: str | None = None
    watcher.start()
    try:
        with live:
            try:
                async for ev in agent.run(conv, mode, cancel):
                    # UX01：Ctrl+O 切换思考展开/折叠——放循环开头，确保思考增量 continue 也能被消费
                    if watcher.expand.is_set():
                        watcher.expand.clear()
                        state.thinking_view.toggle()
                        if state.phase == _Phase.THINKING:
                            _update_dynamic(live, state, renderer, start)
                    if ev.compact is not None:
                        # ch08 F24a/F24b：压缩状态提示，不写入 conversation
                        console.print(Text(format_compact_notice(ev.compact), style="dim"))
                        continue
                    # UX01：旁路展示事件（思考/工具参数增量）先处理，不进 conversation、不影响既有分支
                    if ev.thinking:
                        if state.phase in (_Phase.IDLE, _Phase.THINKING):
                            state.thinking_view.feed(ev.thinking)
                            state.phase = _Phase.THINKING
                            _update_dynamic(live, state, renderer, start)
                        continue
                    if ev.tool_delta:
                        for d in ev.tool_delta:
                            state.tool_progress.feed(d)
                        state.thinking_view.clear()
                        state.phase = _Phase.GENERATING
                        _update_dynamic(live, state, renderer, start)
                        continue
                    if ev.err is not None:
                        ticker.cancel()
                        live.update(Text(""), refresh=True)
                        err_text = str(ev.err)
                        break
                    if ev.tool is not None:
                        if ev.tool.phase == Phase.START:
                            renderer.commit_partial()
                            if not got_text:
                                got_text = True
                                ticker.cancel()
                            # 进入工具执行阶段：清思考/进度旁路，让位给 Running 列表（F3）
                            state.thinking_view.clear()
                            state.tool_progress.clear()
                            state.phase = _Phase.TOOLS
                            state.cur_tools.append(ev.tool)
                        else:
                            # 弹出队首（agent 保证 START/END 按调用序发出，重名不会错位）
                            if state.cur_tools:
                                state.cur_tools.pop(0)
                            live.update(Text(""), refresh=True)
                            live.console.print(tool_line(ev.tool.name, ev.tool.args))
                            live.console.print(
                                tool_result_summary(ev.tool.result, ev.tool.is_error)
                            )
                            if not state.cur_tools:
                                state.phase = _Phase.IDLE  # 工具批执行完，回空闲（等下一轮请求）
                        _update_dynamic(live, state, renderer, start)
                    if ev.approval is not None:
                        # 待批准：暂停 Live 与字节监听，读键交互，完成后 set_result 让 agent 续跑
                        ticker.cancel()
                        live.update(Text(""), refresh=True)  # 先清掉 Live 区残留（Running… 行）
                        live.stop()
                        watcher.stop()
                        try:
                            outcome = await _run_approval(console, ev.approval, cancel)
                        finally:
                            watcher.start()
                            live.start()
                        if outcome is not None:
                            ev.approval.respond.set_result(outcome)
                        # outcome None：cancel 已置位，agent 的待批准等待走取消收尾
                        _update_dynamic(live, state, renderer, start)
                        continue
                    if ev.usage is not None:
                        usage_in += ev.usage.input
                        usage_out += ev.usage.output
                        reasoning_total += ev.usage.reasoning  # UX01 F7
                    if ev.notice:
                        live.update(Text(""), refresh=True)
                        live.console.print(Text(ev.notice, style="dim"))
                    if ev.iter > 0:
                        state.iter = ev.iter
                    if ev.text:
                        if not got_text:
                            got_text = True
                            ticker.cancel()
                        # 正文接管：清思考旁路，进入正文打字机阶段（F3）
                        state.thinking_view.clear()
                        state.phase = _Phase.TEXT
                        renderer.feed(ev.text)
                    if ev.done:
                        break
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 兜底转错误，不崩溃
                ticker.cancel()
                live.update(Text(""), refresh=True)
                err_text = str(e)
            finally:
                ticker.cancel()
            renderer.flush()
    finally:
        watcher.stop()
        ticker.cancel()
    if err_text is not None:
        console.print(f"[bold red]错误: {err_text}[/]")
    elif cancel.is_set():
        console.print("[dim]· 已取消[/]")
    else:
        # UX01 F7：完成统计——扩展现有用时行，附本轮思考 tokens（不重复 in/out，状态栏已承担）
        done_line = f"· 用时 {time.monotonic() - start:.1f}s"
        if reasoning_total:
            done_line += f" · 思考 {_fmt_tok(reasoning_total)} tokens"
        console.print(f"[dim]{done_line}[/]")
    return usage_in, usage_out


async def dispatch_slash(
    ctx: command_pkg.CommandContext, cmd_registry: command_pkg.Registry, text: str
) -> bool:
    """分发一条输入：斜杠命令命中执行 handler，未命中打印未知命令提示（ch10，ch14 修订）。

    返回 True 表示已按命令处理（主循环 continue）；False 表示非命令输入（走对话）。
    命令 handler 抛异常时打印 error_block，不中断主循环（N4 用户可见失败）。
    ch14 F29：`parse` 返回 (name, tail, is_slash)；命中带参命令（args_handler 非空）
    时透传 tail，否则走无参 handler。
    """
    name, tail, is_slash = command_pkg.parse(text)
    if not is_slash:
        return False
    cmd = cmd_registry.lookup(name)
    if cmd is None:
        # parse 返回 ("", True) 即退化输入（纯 "/" 或 "/xx"），提示不拼命令名
        ctx.console.print("[dim]未知命令: 输入 /help 查看可用命令[/]")
        return True
    try:
        if cmd.args_handler is not None:
            await cmd.args_handler(ctx, tail)
        elif cmd.handler is not None:
            await cmd.handler(ctx)
    except Exception as e:  # noqa: BLE001 命令异常兜底：用户可见错误块
        ctx.console.print(error_block(e))
    return True


async def _input_frame(
    provider,
    mode_box: _ModeBox,
    usage_box: _UsageBox,
    cmd_registry,
    coordinator_mode: bool = False,  # ch15：状态栏 Coordinator 标签
    wake_event: asyncio.Event | None = None,  # ch15：Lead 邮箱唤醒信号（哨兵退出输入框）
    wake_app_ref: dict | None = None,  # ch15：输入框 app 共享引用（waker 调 app.exit）
) -> str | None:
    """渲染一次「横线 + ❯ 输入 + 补全菜单 + 横线 + 状态栏」输入框，返回提交文本。

    None 表示退出（Ctrl+C）；空文本表示重新渲染。`/exit` 不再在此特判——
    提交后走主循环命令分发（ch10）。
    Enter 提交（补全激活时直接执行高亮候选）、Ctrl+J 换行、Ctrl+C 退出、
    Esc 忽略/关闭补全菜单；**Shift+Tab 循环切换权限模式**——
    原地更新（改 `_ModeBox` + invalidate 重绘状态栏，不退出 app，避免叠加旧输入框），
    跨轮保持、仅空闲态生效（流式时无输入框）。
    """
    input_buf = Buffer(
        multiline=True,
        completer=CommandCompleter(cmd_registry),  # ch10：补全候选来自命令注册中心
        complete_while_typing=True,
    )

    kb = KeyBindings()
    bind_completion_keys(kb)  # up/down/tab 补全导航（仅激活时消费，F29/F30）

    @kb.add("enter")
    def _submit(event) -> None:
        _submit_selected(event.app)  # 补全激活时执行高亮候选；否则提交输入框文本

    @kb.add("c-j")
    def _newline(event) -> None:
        input_buf.insert_text("\n")

    @kb.add("c-c")
    def _quit(event) -> None:
        event.app.exit(result=None)

    @kb.add("escape")
    def _noop(event) -> None:
        # 补全激活时 ESC 关闭菜单、文本保留（F31）；空闲态 Esc 忽略
        if event.app.current_buffer.complete_state is not None:
            event.app.current_buffer.cancel_completion()
            event.app.invalidate()

    @kb.add("s-tab")
    def _shift_tab(event) -> None:
        # 原地更新权限模式：仅改状态栏并 invalidate 重绘，不退出 app——
        # 非全屏 app 退出不会清理旧渲染，若走「退出→主循环重渲染」会在每次
        # Shift+Tab 时叠加一组旧输入框（旧横线/❯/状态栏不消失）。
        mode_box.mode = next_mode(mode_box.mode)
        event.app.invalidate()

    # 状态栏：左侧常驻权限模式（取代 provider 名）+ 右侧模型名与累计用量。
    # 用 lambda 动态求值，Shift+Tab 改 mode 后 invalidate 即可原地更新。
    layout = Layout(
        HSplit(
            [
                Window(char="─", height=1, dont_extend_height=True, style="dim"),
                VSplit(
                    [
                        Window(
                            FormattedTextControl(FormattedText([("bold green", "❯ ")])),
                            width=2,
                            dont_extend_width=True,
                        ),
                        Window(
                            BufferControl(input_buf),
                            wrap_lines=True,
                            dont_extend_height=True,
                        ),
                    ]
                ),
                # ch10：补全菜单紧贴输入框下方、状态栏上方（N6）；无候选时高度为 0
                CompletionsMenu(max_height=MAX_ROWS),
                Window(char="─", height=1, dont_extend_height=True, style="dim"),
                Window(
                    FormattedTextControl(
                        lambda: status_text(
                            mode_box.mode,
                            provider.model,
                            usage_box.usage_in,
                            usage_box.usage_out,
                            coordinator_mode,  # ch15
                        )
                    ),
                    height=1,
                    dont_extend_height=True,
                    style="dim",
                ),
            ]
        ),
        focused_element=input_buf,
    )
    # 禁用 ptk 的 resize 响应（不追求输入框随窗口自适应，界面保持稳定）：
    # - 非全屏 ptk 的 `_on_resize` 用「上次渲染的光标位置」erase，窗口变化导致 scrollback
    #   滚动后擦错范围，会残留旧宽度的横线、甚至误清对话内容（上漂）。
    # - 故禁用其尺寸轮询（Windows）与 SIGWINCH（Linux）触发，窗口调整时输入框不重绘、
    #   保持原样；下次按键/提交时才按新宽度干净重绘一次（此时无 resize 干扰，不会残留）。
    app = Application(
        layout=layout,
        full_screen=False,
        key_bindings=kb,
        mouse_support=False,
        terminal_size_polling_interval=None,
    )
    app._on_resize = lambda: None  # type: ignore[method-assign]  # 禁 SIGWINCH 触发的内置重绘

    # ch15：Lead 邮箱唤醒——后台 task 等 wake_event，触发后以哨兵退出输入框（F41b）。
    # 取舍：用户正在输入时会被自动回合打断、丢失输入框内容（<team-update> 已含消息摘要）。
    waker: asyncio.Task | None = None
    if wake_event is not None and wake_app_ref is not None:
        wake_app_ref["app"] = app

        async def _waker() -> None:
            await wake_event.wait()
            wake_event.clear()
            ref = wake_app_ref.get("app")
            if ref is not None:
                try:
                    ref.exit(result=TEAM_AUTONOMOUS)
                except Exception:  # noqa: BLE001,S110 输入框已退出则忽略
                    pass

        waker = asyncio.create_task(_waker())
    try:
        text = await app.run_async()
    finally:
        if waker is not None:
            waker.cancel()
    if text is None:
        return None
    return text.strip()


async def _dispatch_session_start(hook_engine, runtime, root: str, mode) -> None:
    """ch12：SessionStart hook——env context 装配完毕、首条 user 消息进入历史之前（F9）。

    `hook_engine` 为 None 时不动作；注入的 prompt 写入 runtime 队列（F33）。
    """
    if hook_engine is None:
        return
    result = await hook_engine.dispatch(
        HookEvent.SESSION_START,
        hook_base_payload(runtime=runtime, root=root, mode=mode, event=HookEvent.SESSION_START),
    )
    if result.injected_prompts:
        runtime.append_reminders(result.injected_prompts)


async def _dispatch_user_prompt_submit(
    hook_engine, runtime, root: str, mode, text: str
) -> tuple[bool, str]:
    """ch12：UserPromptSubmit hook——写入对话历史之前，可拦截（F32）。

    返回 (blocked, 展示文本)：blocked 时调用方不写历史、不发起请求（非全屏重画输入框）。
    放行时把注入的 prompt 写入 runtime 队列。
    """
    result = await hook_engine.dispatch(
        HookEvent.USER_PROMPT_SUBMIT,
        hook_base_payload(runtime=runtime, root=root, mode=mode, event=HookEvent.USER_PROMPT_SUBMIT)
        | {"prompt": text},
    )
    if result.blocked:
        return True, f"[hook {result.blocking_hook_name}] {result.reason}"
    if result.injected_prompts:
        runtime.append_reminders(result.injected_prompts)
    return False, ""


async def run(
    cfg: Config,
    registry: Registry,
    engine: Engine,
    runtime: SessionRuntime,
    *,
    cmd_registry: command_pkg.Registry,
    writer: session_pkg.Writer | None = None,
    mem_mgr: mem_pkg.Manager | None = None,
    instruction_text: str = "",
    memory_text: str = "",
    sessions_dir: str = "",
    catalog=None,  # ch11：skills.Catalog，注入 CommandContext 供 /skill 与 executor 用
    hook_engine=None,  # ch12：hook.Engine | None（cli 启动期加载注入）
    task_mgr=None,  # ch13：task.Manager | None（后台任务管理）
    subagent_catalog=None,  # ch13：subagent.Catalog | None（Agent 工具定义目录）
    worktree_mgr=None,  # ch14：worktree.Manager | None（Worktree 隔离）
    team_mgr=None,  # ch15：team.Manager | None（/team 命令 + Lead 邮箱轮询）
    coordinator_mode: bool = False,  # ch15：Coordinator 模式（状态栏标签）
    coordinator_allowed: list[str] | None = None,  # ch15：Coordinator 白名单（None=不限）
    coordinator_prompt: str = "",  # ch15：Coordinator 纪律段（追加主 Agent 系统提示）
) -> int:
    """对话主循环（异步）：banner → 选 provider → 循环（输入框 + Agent Loop 回复）。

    - 12 条 slash 命令走 `cmd_registry`（ch10：Registry + CommandContext + handler）；
    - `mode` 跨轮保持（Shift+Tab 循环切换权限模式）；`usage_box` 会话累计，跨轮累加；
    - `runtime` 由 cli 注入，跨 run 持有上下文管理状态；provider 选定后填 context_window；
    - ch09：`writer` 会话 JSONL 存档、`mem_mgr` 记忆管理器、指令/记忆文本注入 Agent；
      `/resume` 经 holder 替换会话状态后，主循环后续消息追加到恢复的会话；
    - 命令分发只发生在主循环空闲阶段（流式期间无输入框），天然互斥（N3a）。
    """
    if len(cfg.providers) == 1:
        provider_cfg = cfg.providers[0]
    else:
        provider_cfg = choose_provider(cfg.providers)
    provider = new_provider(provider_cfg)
    runtime.context_window = effective_context_window(provider_cfg)  # ch08 F30/F31
    root = os.getcwd()
    # ch09：会话存档 Writer + 记忆管理器接线（provider 选定后才可知 model）
    if writer is None:
        writer = session_pkg.Writer(runtime.session.session_dir)
    writer.set_model(provider.model)
    if mem_mgr is not None:
        mem_mgr.set_provider(provider, provider.model)
    holder = _SessionHolder(
        conv=Conversation(on_append=writer.on_append, on_replace=writer.on_replace),
        writer=writer,
        ses_ctx=runtime.session,
    )

    console = Console(highlight=False)
    console.print(render_banner(__version__, root))

    mode_box = _ModeBox(engine.start_mode)
    # 常驻 Agent：注入记忆管理器与指令/记忆文本（ch09 T15）；跨轮复用
    agent = Agent(
        provider,
        registry,
        __version__,
        engine,
        runtime=runtime,
        memory_manager=mem_mgr,
        instruction_text=instruction_text,
        memory_text=memory_text,
        catalog=catalog,  # ch11 F21：第一层 skills-catalog 注入
        hook_engine=hook_engine,  # ch12 F30：Hook 引擎构造期注入
        # ch15：主 Agent 不 exclude Team 协作工具——Lead 需要 TeamSendMessage/TeamTask* 协调队员
        allowed_tools=coordinator_allowed,  # ch15：Coordinator 白名单（None=不限制）
        system_suffix=coordinator_prompt,  # ch15：Coordinator 纪律段
    )
    # ch13：Agent 工具 parent 回填（主 Agent 在 run 内构造，cli 无法在注册时拿到）
    at = registry.get("Agent")
    if at is not None and hasattr(at, "set_parent"):
        at.set_parent(agent)
    if mem_mgr is not None:
        # F32：记忆更新成功落盘后刷新 Agent 的 memory_text 快照
        mem_mgr.add_updated_callback(agent.refresh_memory_text)
    usage_box = _UsageBox()
    exit_flag: list[bool] = [False]
    # ch15：Lead 邮箱轮询 + 自动唤醒（team_mgr 非 None 时启动；_input_app_ref 供 waker 退出输入框）
    lead_mail_event = asyncio.Event()
    consume_task: asyncio.Task | None = None
    _input_app_ref: dict = {}
    if team_mgr is not None:
        consume_task = asyncio.create_task(consume_lead_mail(team_mgr, runtime, lead_mail_event))
    sessions_dir = sessions_dir or str(Path(root) / ".novacode" / "sessions")
    # ch14：Worktree active_cwd（空=进程 cwd）。`/worktree enter/exit` 经
    # CommandContext.set_active_cwd 更新；主 Agent 每次 Run 前用它包 ctx。
    active_cwd_box: list[str] = [""]

    def _set_active_cwd(cwd: str) -> None:
        active_cwd_box[0] = cwd

    def _effective_cwd() -> str:
        return active_cwd_box[0] or str(Path.cwd())

    async def _stream_reply_cwd(
        agent: Agent, conv: Conversation, console: Console, mode: Mode, cancel: asyncio.Event
    ) -> tuple[int, int]:
        """带 ctx cwd 的 _stream_reply：工具相对路径按 active_cwd 解析（ch14 F18）。"""
        with with_cwd(_effective_cwd()):
            return await _stream_reply(agent, conv, console, mode, cancel)

    # 启动装配：若存在持久化的 Worktree 会话，恢复 active_cwd（spec F32）。
    # 注：current_session 是 Manager 的实例属性（非方法）。
    if worktree_mgr is not None:
        sess = worktree_mgr.current_session
        if sess is not None:
            active_cwd_box[0] = sess.worktree_path
    # ch11：provider 选定后构造 Skill executor（Skill 命令 handler 经 ctx.executor 执行）
    executor = None
    if catalog is not None:
        executor = skills_pkg.Executor(
            catalog,
            runtime.active_skills,
            registry,
            provider,
            provider_cfg,
            engine,
            __version__,
            runtime,
        )

    async def run_callback() -> None:
        """/do /review 的「立即执行」入口：发起一次 Agent Loop 回复。"""
        cancel = asyncio.Event()
        used = await _stream_reply_cwd(agent, holder.conv, console, mode_box.mode, cancel)
        usage_box.usage_in += used[0]
        usage_box.usage_out += used[1]

    # ch12：SessionStart hook——首次进入会话、env context 装配完毕、首条 user 消息进入历史之前（F9）
    await _dispatch_session_start(hook_engine, runtime, root, mode_box.mode)

    while True:
        # /exit 置位 exit_flag 后，dispatch_slash 返回 True → continue 回循环顶部，
        # 必须先检查再渲染输入框，否则会多画一个输入框阻塞等输入（ch10 bug 修复）。
        if exit_flag[0]:
            break
        text = await _input_frame(
            provider,
            mode_box,
            usage_box,
            cmd_registry,
            coordinator_mode=coordinator_mode,  # ch15
            wake_event=lead_mail_event,  # ch15
            wake_app_ref=_input_app_ref,  # ch15
        )
        if text is None or exit_flag[0]:
            break
        if text == TEAM_AUTONOMOUS:
            # ch15 F41b：队员消息自动唤醒——合成 user 消息开一轮 Run（不写用户原文）
            if task_mgr is not None:
                await _inject_task_notifications(task_mgr, runtime)
            holder.conv.add_user("[team-update] 队员发来新消息，请按 Coordinator 流程处理...")
            cancel = asyncio.Event()
            used = await _stream_reply_cwd(agent, holder.conv, console, mode_box.mode, cancel)
            usage_box.usage_in += used[0]
            usage_box.usage_out += used[1]
            continue
        ctx = command_pkg.CommandContext(
            holder=holder,
            registry=registry,
            mode_box=mode_box,
            agent=agent,
            console=console,
            exit_flag=exit_flag,
            run_callback=run_callback,
            provider=provider,
            usage_box=usage_box,
            mem_mgr=mem_mgr,
            runtime=runtime,
            root=root,
            sessions_dir=sessions_dir,
            catalog=catalog,  # ch11
            executor=executor,  # ch11
            hook_engine=hook_engine,  # ch12
            worktree_mgr=worktree_mgr,  # ch14
            set_active_cwd=_set_active_cwd,  # ch14
            team_mgr=team_mgr,  # ch15
        )
        if await dispatch_slash(ctx, cmd_registry, text):
            continue
        if not text:
            continue
        # ch12：UserPromptSubmit hook——写入对话历史之前，可拦截（F32）
        if hook_engine is not None:
            blocked, block_text = await _dispatch_user_prompt_submit(
                hook_engine, runtime, root, mode_box.mode, text
            )
            if blocked:
                # 非全屏：打印提示后 continue（不写历史、不发起请求），主循环自然重画输入框
                console.print(Text(block_text, style="bold red"))
                continue
        # ch13：每轮输入后、调 Agent.run 之前，注入后台任务完成通知
        if task_mgr is not None:
            await _inject_task_notifications(task_mgr, runtime)
        holder.conv.add_user(text)
        cancel = asyncio.Event()
        used = await _stream_reply_cwd(agent, holder.conv, console, mode_box.mode, cancel)
        usage_box.usage_in += used[0]
        usage_box.usage_out += used[1]

    if consume_task is not None:  # ch15：退出时取消 Lead 邮箱轮询
        consume_task.cancel()
    return 0
