"""补全单元测试：CommandCompleter 候选生成（ch10 T11）。

覆盖前缀匹配、非 / 开头不激活、换行不激活、零匹配无候选、start_position 语义。
"""

from __future__ import annotations

from prompt_toolkit.document import Document

from novacode.command import Registry, register_builtins
from novacode.tui.completion import CommandCompleter


def _items(completer: CommandCompleter, text: str) -> list:
    doc = Document(text)
    return list(completer.get_completions(doc, None))  # type: ignore[arg-type]


def _plain(display) -> str:
    """把 Completion.display（str 或 FormattedText）转成纯文本。"""
    if isinstance(display, str):
        return display
    return "".join(t for _, t in display)


def _reg() -> Registry:
    reg = Registry()
    register_builtins(reg)
    return reg


def test_completer_prefix_match():
    c = CommandCompleter(_reg())
    items = _items(c, "/s")
    assert sorted(x.text for x in items) == ["session", "skill", "status"]  # ch11 加 /skill
    for x in items:
        assert x.text in _plain(x.display)  # 候选显示包含命令名


def test_completer_start_position_dynamic():
    """ch10 bug 修复：start_position 随「/ 后已输入长度」变化。

    / → 0（不删 /）、/h → -1（替换 h）、/he → -2（替换 he），保证方向键浏览
    时输入框显示完整的 /command，不丢斜杠也不残留多余前缀。
    """
    c = CommandCompleter(_reg())
    for text, expected in [("/", 0), ("/h", -1), ("/he", -2), ("/help", -4)]:
        items = _items(c, text)
        assert items, text
        assert items[0].start_position == expected, (text, expected)


def test_completer_full_name_matches_itself():
    c = CommandCompleter(_reg())
    items = _items(c, "/session")
    assert [x.text for x in items] == ["session"]


def test_completer_inactive_without_slash():
    c = CommandCompleter(_reg())
    assert _items(c, "hello") == []
    assert _items(c, "") == []


def test_completer_inactive_when_multiline():
    """F32a：输入含换行符时不激活。"""
    c = CommandCompleter(_reg())
    assert _items(c, "/s\n") == []


def test_completer_zero_match():
    """F32b：零匹配 → 无候选（菜单不显示）。"""
    c = CommandCompleter(_reg())
    assert _items(c, "/xx") == []


def test_completer_all_when_slash_only():
    """F24：仅输入 "/" → 全部候选。"""
    c = CommandCompleter(_reg())
    items = _items(c, "/")
    assert len(items) == 15  # ch12 内置命令含 /hooks；ch14 加 /worktree；ch15 加 /team
