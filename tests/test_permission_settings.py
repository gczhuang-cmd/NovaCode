"""配置加载与映射单测：降级、友好名、类别、目标提取（N5 / F3）。"""

import pytest

from novacode.llm import ToolCall
from novacode.permission import Category
from novacode.permission.settings import (
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)


def test_load_missing_file_returns_empty(tmp_path):
    s = load_settings(str(tmp_path / "nope.yaml"))
    assert s.default_mode == "" and s.permissions.allow == [] and s.permissions.deny == []


def test_load_invalid_yaml_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("[[[  not: [yaml", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(str(p))


def test_load_valid_yaml(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(
        "default_mode: plan\npermissions:\n  allow:\n    - 'Bash(git *)'\n  deny:\n    - 'Read(.env)'\n",
        encoding="utf-8",
    )
    s = load_settings(str(p))
    assert s.default_mode == "plan"
    assert s.permissions.allow == ["Bash(git *)"]
    assert s.permissions.deny == ["Read(.env)"]


def test_to_rule_set_skips_invalid(tmp_path, capsys):
    p = tmp_path / "settings.yaml"
    p.write_text(
        "permissions:\n  allow:\n    - 'Bash(git *)'\n    - '((bad'\n    - 42\n",
        encoding="utf-8",
    )
    rs = to_rule_set(load_settings(str(p)))
    assert len(rs.allow) == 1 and rs.allow[0].tool == "Bash"
    assert rs.deny == []
    # ch12 F4：失败 rule 有声跳过——stderr 含 parse failed
    captured = capsys.readouterr()
    assert "parse failed" in captured.err


def test_friendly_name():
    assert friendly_name("bash") == "Bash"
    assert friendly_name("read_file") == "Read"
    assert friendly_name("write_file") == "Write"
    assert friendly_name("edit_file") == "Edit"
    assert friendly_name("glob") == "Glob"
    assert friendly_name("grep") == "Grep"
    assert friendly_name("unknown_tool") == "unknown_tool"


def test_categorize():
    assert categorize("read_file", True) == Category.READ
    assert categorize("write_file", True) == Category.READ  # read_only 优先
    assert categorize("write_file", False) == Category.WRITE
    assert categorize("edit_file", False) == Category.WRITE
    assert categorize("bash", False) == Category.EXEC
    assert categorize("unknown", False) == Category.EXEC  # N7 最严


def _call(name, raw: str) -> ToolCall:
    return ToolCall(id="id", name=name, input=raw)


def test_extract_target_path_tools():
    t, is_file, ok = extract_target(_call("read_file", '{"path": "a/b.txt"}'))
    assert (t, is_file, ok) == ("a/b.txt", True, True)
    t, is_file, ok = extract_target(_call("write_file", '{"path": "x.txt", "content": "hi"}'))
    assert (t, is_file, ok) == ("x.txt", True, True)
    t, is_file, ok = extract_target(_call("edit_file", '{"path": "y"}'))
    assert (t, is_file, ok) == ("y", True, True)


def test_extract_target_glob_grep():
    t, is_file, ok = extract_target(_call("glob", '{"pattern": "**/*.py"}'))
    assert (t, is_file, ok) == (".", True, True)  # 缺 path → 搜索根默认当前目录
    t, is_file, ok = extract_target(_call("grep", '{"pattern": "x", "path": "src"}'))
    assert (t, is_file, ok) == ("src", True, True)


def test_extract_target_bash():
    t, is_file, ok = extract_target(_call("bash", '{"command": "git status"}'))
    assert (t, is_file, ok) == ("git status", False, True)


def test_extract_target_failure():
    # 非 JSON
    _, is_file, ok = extract_target(_call("read_file", "not-json"))
    assert ok is False
    # 文件类缺 path
    _, is_file, ok = extract_target(_call("read_file", '{"path": ""}'))
    assert (is_file, ok) == (True, False)
    # bash 缺 command
    _, is_file, ok = extract_target(_call("bash", "{}"))
    assert (is_file, ok) == (False, False)
    # 未知工具
    _, is_file, ok = extract_target(_call("mystery", "{}"))
    assert (is_file, ok) == (False, False)
