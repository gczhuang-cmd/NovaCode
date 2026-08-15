"""流式期间的旁路渲染：思考内容滚动/展开 + 工具参数生成进度。

纯渲染模块，无 I/O、无终端访问。`_ThinkingView` 维护思考内容的最近 N 行与全文
（双缓冲，避免长思考的 O(n²) 拼接）；`_ToolProgress` 按工具 index 聚合参数增量
字符数。二者都只供 `run.py` 的 Live 动态区消费，内容不进对话历史/JSONL（F6/N5）。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from rich.text import Text

from ..llm import ToolDelta

# 滚动态默认保留行数（spec F1，N=3）
DEFAULT_THINKING_LINES = 3


def fmt_char_count(n: int) -> str:
    """紧凑字符数：≥1000 显示 `12.3K`（整数运算避免浮点四舍五入陷阱）。"""
    if n >= 1000:
        tenths = (n + 50) // 100  # 四舍五入到 0.1K
        whole, frac = divmod(tenths, 10)
        return f"{whole}K" if frac == 0 else f"{whole}.{frac}K"
    return str(n)


@dataclass
class _ThinkingView:
    """思考渲染状态机：滚动最近 N 行 / 展开全文 / 让位清空。

    - `_recent`：已完成的完整行（去换行），deque 保留最近 N 行，供滚动态显示；
    - `_full`：全部完整行，供展开态显示全文；
    - `_partial`：未完成尾部行（尚无换行符），两种态都拼接在末尾。
    """

    max_lines: int = DEFAULT_THINKING_LINES
    expanded: bool = False  # Ctrl+O 展开态

    _recent: deque[str] = field(default_factory=lambda: deque(maxlen=DEFAULT_THINKING_LINES))
    _full: list[str] = field(default_factory=list)
    _partial: str = ""

    def feed(self, chunk: str) -> None:
        """喂入一段思考增量：按换行拆行，整行入缓冲，不完整尾部留 `_partial`。"""
        self._partial += chunk
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            if line:
                self._full.append(line)
                self._recent.append(line)

    def render(self) -> Text | None:
        """当前应显示的思考文本；空则 None（调用方回退到思考中计时）。"""
        lines = self._full if self.expanded else list(self._recent)
        body = "\n".join(lines)
        if self._partial:
            body = f"{body}\n{self._partial}" if body else self._partial
        if not body:
            return None
        return Text(body, style="dim")

    def toggle(self) -> None:
        """切换展开/滚动。"""
        self.expanded = not self.expanded

    def clear(self) -> None:
        """阶段让位：正文/工具增量到达时清空（F3）。"""
        self._recent.clear()
        self._full.clear()
        self._partial = ""


@dataclass
class _ToolProgress:
    """工具参数生成进度：按 index 聚合 name 与累计字符数（tool_delta 增量）。"""

    by_index: dict[int, dict] = field(default_factory=dict)

    def feed(self, d: ToolDelta) -> None:
        """聚合一段增量：name 非空时覆盖（首段携带），chars 累加增量长度。"""
        entry = self.by_index.setdefault(d.index, {"name": "", "chars": 0})
        if d.name:
            entry["name"] = d.name
        entry["chars"] += len(d.args)

    def render(self) -> Text | None:
        """当前进度文本：多工具逐行「⚙ 正在生成 X 参数…(已 N 字符)」。"""
        if not self.by_index:
            return None
        lines = [
            f"⚙ 正在生成 {e['name'] or '工具'} 参数…(已 {fmt_char_count(e['chars'])} 字符)"
            for e in self.by_index.values()
        ]
        return Text("\n".join(lines), style="bold cyan")

    def clear(self) -> None:
        """工具调用开始执行时清空（进度让位给 Running 列表）。"""
        self.by_index.clear()
