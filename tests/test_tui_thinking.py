"""tui/thinking.py 纯渲染单测：思考滚动/展开/清空、工具进度聚合、字符格式化。"""

from novacode.llm import ToolDelta
from novacode.tui.thinking import _ThinkingView, _ToolProgress, fmt_char_count


def test_fmt_char_count():
    assert fmt_char_count(0) == "0"
    assert fmt_char_count(999) == "999"
    assert fmt_char_count(1000) == "1K"
    assert fmt_char_count(12300) == "12.3K"
    assert fmt_char_count(12350) == "12.4K"


def test_thinking_feed_splits_lines_and_partial():
    v = _ThinkingView(max_lines=3)
    v.feed("第一行\n第二行\n第三行未完成")
    assert v.render().plain == "第一行\n第二行\n第三行未完成"
    # partial 续接成完整行「第三行未完成完」，再开新行「第四行」
    v.feed("完\n第四行")
    assert v.render().plain == "第一行\n第二行\n第三行未完成完\n第四行"


def test_thinking_scroll_keeps_recent_lines():
    v = _ThinkingView(max_lines=3)
    for i in range(1, 6):
        v.feed(f"第{i}行\n")
    # 滚动态：只保留最近 3 行
    assert v.render().plain == "第3行\n第4行\n第5行"
    # 展开态：全文可见
    v.expanded = True
    assert v.render().plain == "第1行\n第2行\n第3行\n第4行\n第5行"


def test_thinking_toggle_and_clear():
    v = _ThinkingView(max_lines=3)
    assert v.render() is None  # 空态
    v.feed("思考内容\n")
    assert v.expanded is False
    v.toggle()
    assert v.expanded is True
    v.toggle()
    assert v.expanded is False
    v.clear()
    assert v.render() is None


def test_tool_progress_aggregates_by_index():
    tp = _ToolProgress()
    tp.feed(ToolDelta(index=0, name="write_file", args="abcd"))
    tp.feed(ToolDelta(index=0, name="", args="efgh"))  # name 空不覆盖，chars 累加
    tp.feed(ToolDelta(index=1, name="bash", args="echo hi"))
    rendered = tp.render().plain
    assert "write_file" in rendered
    assert "已 8 字符" in rendered
    assert "bash" in rendered
    assert "已 7 字符" in rendered


def test_tool_progress_clear():
    tp = _ToolProgress()
    tp.feed(ToolDelta(index=0, name="write_file", args="x"))
    assert tp.render() is not None
    tp.clear()
    assert tp.render() is None
