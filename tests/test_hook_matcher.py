"""hook.matcher 单测：get_by_path / eval_condition（ch12 T7）。"""

import re

from novacode.hook.matcher import eval_condition, get_by_path
from novacode.hook.rule import AtomCondition, CombineMode, Condition
from novacode.permission.matcher import ExactMatcher, GlobMatcher, RegexMatcher


def test_get_by_path_scalar():
    p = {"tool_input": {"path": "a/b.py", "n": 3, "flag": False}, "tool_name": "write_file"}
    assert get_by_path(p, "tool_name") == "write_file"
    assert get_by_path(p, "tool_input.path") == "a/b.py"
    assert get_by_path(p, "tool_input.n") == "3"
    assert get_by_path(p, "tool_input.flag") == "False"


def test_get_by_path_missing_or_non_dict():
    p = {"tool_input": {"path": "a/b.py"}}
    assert get_by_path(p, "tool_input.command") == ""
    assert get_by_path(p, "nope.deep") == ""
    assert get_by_path(p, "") == ""
    assert get_by_path(p, "tool_input") == '{"path": "a/b.py"}'  # 嵌套对象 JSON 字典序


def _cond(mode, *atoms):
    return Condition(mode=mode, atoms=list(atoms))


def test_eval_condition_none_is_true():
    assert eval_condition(None, {}) is True


def test_eval_condition_all_of():
    c = _cond(
        CombineMode.ALL_OF,
        AtomCondition(field="tool_name", matcher=ExactMatcher("write_file")),
        AtomCondition(field="tool_input.path", matcher=GlobMatcher("**/*.py", is_command=False)),
    )
    assert eval_condition(c, {"tool_name": "write_file", "tool_input": {"path": "a/b.py"}})
    assert not eval_condition(c, {"tool_name": "read_file", "tool_input": {"path": "a/b.py"}})
    assert not eval_condition(c, {"tool_name": "write_file", "tool_input": {"path": "a.md"}})


def test_eval_condition_any_of():
    rx = re.compile("(?i)delete")
    c = _cond(
        CombineMode.ANY_OF,
        AtomCondition(field="prompt", matcher=RegexMatcher("(?i)delete", rx)),
        AtomCondition(field="prompt", matcher=ExactMatcher("confirm")),
    )
    assert eval_condition(c, {"prompt": "please delete the file"})
    assert eval_condition(c, {"prompt": "confirm"})
    assert not eval_condition(c, {"prompt": "hi"})


def test_eval_condition_missing_path_is_empty_string():
    """缺路径 → 空串 → exact "" 命中（F13：路径不存在按空串处理，不报错）。"""
    c = _cond(CombineMode.ALL_OF, AtomCondition(field="missing.field", matcher=ExactMatcher("")))
    assert eval_condition(c, {})
