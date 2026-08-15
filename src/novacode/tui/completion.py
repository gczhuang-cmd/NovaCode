"""prompt_toolkit 自动补全：候选来源 + 键位绑定（ch10 T11）。

用原生 `Completer` + `CompletionMenu` 控件（零匹配不显示菜单、删空自动关闭、
超行滚动均由 prompt_toolkit 处理）。候选由 command.Registry 前缀匹配提供
（F24/F25）；`_input_frame` 在 Buffer 上启用 `complete_while_typing`。
"""

from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import has_completions
from prompt_toolkit.key_binding import KeyBindings

# 补全菜单最大可见行数（F5/N5，超过内部滚动）
MAX_ROWS = 8


class CommandCompleter(Completer):
    """按输入框文本前缀匹配注册命令名（F24/F25/F32a）。"""

    def __init__(self, reg) -> None:
        self._reg = reg

    def get_completions(self, document, complete_event):
        text = document.text
        # F26：首字符非 "/"（含删空）→ 无候选，菜单关闭；F32a：含换行不激活
        if not text.startswith("/") or "\n" in text:
            return
        items = self._reg.prefix_match(text)
        if not items:
            return
        width = max(len(c.name) for c in items)
        # start_position：补全替换「/ 之后光标前的已输入部分」——/he 应替换 he（2 字符）。
        # 固定 -1 会出错：/ 时删掉斜杠（显示 help 缺 /）、/he 时只删 e（显示 /hhelp）。
        n = max(0, len(document.text_before_cursor) - 1)
        for c in items:
            yield Completion(
                text=c.name,
                start_position=-n,
                display=f"/{c.name.ljust(width)}  {c.description}",
            )


def _submit_selected(app) -> None:
    """补全激活时执行高亮候选；否则正常提交输入框文本（F30）。"""
    buf = app.current_buffer
    if buf.complete_state is not None and buf.complete_state.current_completion is not None:
        app.exit(result="/" + buf.complete_state.current_completion.text)
    else:
        app.exit(result=buf.text)


def bind_completion_keys(kb: KeyBindings) -> None:
    """给输入框 KeyBindings 添加补全导航键位（F29/F30/F32）。

    up/down **无条件绑定**：补全激活时导航高亮（complete_next/previous），否则
    回退光标移动（cursor_up/down）——避免 prompt_toolkit 默认 `auto_down` 在无补全
    且光标位于最后一行时触发 `history_forward`（把历史输入填进来的意外行为）。
    tab 仅补全激活时消费（filter=has_completions），关闭时走默认。
    enter/escape 由 `_input_frame` 的现有绑定合并处理（enter 走 `_submit_selected`、
    escape 关菜单）。
    """

    @kb.add("up")
    def _up(event) -> None:
        buf = event.app.current_buffer
        if buf.complete_state is not None:
            buf.complete_previous()
        else:
            buf.cursor_up()
        event.app.invalidate()

    @kb.add("down")
    def _down(event) -> None:
        buf = event.app.current_buffer
        if buf.complete_state is not None:
            buf.complete_next()
        else:
            buf.cursor_down()
        event.app.invalidate()

    @kb.add("tab", filter=has_completions)
    def _tab(event) -> None:
        _submit_selected(event.app)
