"""各状态的渲染拼装：消息块、状态栏、错误样式、markdown 定型、工具行。"""

from __future__ import annotations

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

# 工具结果摘要在 UI 显示的最大行数（回灌给模型的内容仍由工具级上限控制）
_RESULT_MAX_LINES = 8


def user_block(text: str) -> Text:
    """用户输入块：加粗、带前缀圆点，无文字标签。"""
    return Text("● " + text, style="bold")


def error_block(err: Exception) -> Text:
    """错误块：红色加粗，与正常消息可区分。"""
    return Text("● " + str(err), style="bold red")


def assistant_markdown(reply: str) -> Group:
    """助手回复定型：整段 markdown 渲染（代码块/列表/强调），前缀圆点。"""
    return Group(Text("● "), Markdown(reply))


def tool_line(name: str, args: str) -> Text:
    """Claude Code 风格工具行：`● name(args)`，青色圆点 + 加粗工具名与参数。"""
    return Text("● ", style="bold cyan") + Text(f"{name}({args})", style="bold")


def tool_result_summary(result: str, is_error: bool) -> Padding:
    """工具结果摘要：缩进 + `⎿`，错误红色、成功暗色，UI 截断 ~8 行。"""
    style = "bold red" if is_error else "dim"
    lines = result.splitlines()
    if len(lines) > _RESULT_MAX_LINES:
        lines = lines[:_RESULT_MAX_LINES] + ["…[truncated]"]
    return Padding(Text("⎿ ") + Text("\n".join(lines), style=style), (0, 0, 0, 2))


def approval_lines(req, cursor: int) -> list[tuple[str, str]]:
    """待批准块的行列表（(style, text)）：工具名 + 参数 + 触发原因 + 三选菜单 + 操作提示。

    每行以 `\n` 结尾（末行除外）——prompt_toolkit 的 `FormattedTextControl` 渲染 fragment
    列表时默认把各段文本**拼在同一行**，不显式换行会把整块挤成一行、再被终端 wrap 成
    重叠的难看效果。`cursor` 高亮当前菜单项（0=允许本次、1=永久、2=拒绝本次）。
    """
    lines: list[tuple[str, str]] = [
        ("bold cyan", f"● {req.name}({req.args})\n"),
        ("dim", f"    {req.reason}\n"),
        ("", "是否继续?\n"),
    ]
    options = ("1. 允许本次", "2. 永久允许（写入本地配置）", "3. 拒绝本次")
    for i, opt in enumerate(options):
        prefix, style = ("> ", "bold green") if i == cursor else ("  ", "")
        lines.append((style, prefix + opt + "\n"))
    lines.append(("dim", "↑↓ 选择 · 回车确认 · 1/2/3 直选 · Esc 取消"))
    return lines
