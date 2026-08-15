"""命令输入解析单测（ch10 T3）。"""

from __future__ import annotations

import pytest

from novacode.command.dispatch import parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ("", "", False)),
        ("   ", ("", "", False)),
        ("hello", ("", "", False)),
        ("/", ("", "", True)),
        ("/help", ("help", "", True)),
        ("  /HELP  ", ("help", "", True)),
        ("/help xx", ("help", "xx", True)),  # ch14 F29：带参数命令返回 tail
        ("/help  ", ("help", "", True)),  # 尾部空白 strip 后为空
        ("/ /help", ("", "", True)),  # 无命令名
        ("/exit", ("exit", "", True)),
        ("/worktree create foo", ("worktree", "create foo", True)),  # ch14
    ],
)
def test_parse_samples(text: str, expected: tuple[str, str, bool]):
    assert parse(text) == expected


def test_parse_double_slash_misses():
    """//double → 是斜杠命令但 lookup 必然 miss → 走未知命令分支。"""
    name, tail, is_slash = parse("//double")
    assert is_slash is True
    assert name == "/double"
    assert tail == ""
