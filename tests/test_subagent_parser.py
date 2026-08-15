"""subagent 解析器与字段校验单测（ch13 T3）。"""

from __future__ import annotations

import pytest

from novacode.permission import Mode
from novacode.subagent.definition import Source
from novacode.subagent.parser import AGENT_NAME_REGEX, parse_definition, parse_file

_BASE = """---
name: explore
description: 只读探索 Agent
maxTurns: 30
---

你是一个搜索专家。
"""


def _d(text: str, source: Source = Source.BUILTIN, path: str = "test.md"):
    return parse_definition(text.encode("utf-8"), path, source)


def test_full_frontmatter():
    d = _d(
        """---
name: Explore
description: 只读探索
tools: [read_file, grep]
disallowedTools: [write_file, edit_file]
model: sonnet
maxTurns: 10
permissionMode: acceptEdits
background: true
---

body text
"""
    )
    assert d.name == "Explore"
    assert d.description == "只读探索"
    assert d.tools == ["read_file", "grep"]
    assert d.disallowed_tools == ["write_file", "edit_file"]
    assert d.model == "sonnet"
    assert d.max_turns == 10
    assert d.permission_mode == Mode.ACCEPT_EDITS
    assert d.dont_ask is False
    assert d.background is True
    assert d.system_prompt.strip() == "body text"
    assert d.file_path == "test.md"
    assert d.source is Source.BUILTIN
    assert d.is_fork() is False


def test_minimal_required():
    d = _d("---\nname: a\ndescription: d\n---\n")
    assert d.name == "a"
    assert d.max_turns == 0
    assert d.model == "inherit"
    assert d.permission_mode == Mode.DEFAULT
    assert d.dont_ask is False


def test_permission_mode_dont_ask():
    d = _d("---\nname: auto\ndescription: d\npermissionMode: dontAsk\n---\nbody")
    assert d.dont_ask is True
    assert d.permission_mode == Mode.DEFAULT


def test_permission_mode_plan():
    d = _d("---\nname: p\ndescription: d\npermissionMode: plan\n---\n")
    assert d.permission_mode == Mode.PLAN
    assert d.dont_ask is False


def test_invalid_model_fallback(capsys):
    d = _d("---\nname: bad\ndescription: d\nmodel: gpt-4\n---\n")
    assert d.model == "inherit"
    err = capsys.readouterr().err
    assert 'unknown model "gpt-4"' in err
    assert "defaulting to inherit" in err


def test_invalid_permission_mode_fallback(capsys):
    d = _d("---\nname: bad\ndescription: d\npermissionMode: weirdMode\n---\n")
    assert d.permission_mode == Mode.DEFAULT
    assert d.dont_ask is False
    err = capsys.readouterr().err
    assert 'unknown permissionMode "weirdMode"' in err
    assert "defaulting to default" in err


@pytest.mark.parametrize("name", ["", "1abc", "ab cd", "a" * 33])
def test_invalid_name_raises(name):
    with pytest.raises(ValueError):
        _d(f"---\nname: {name}\ndescription: x\n---\n")


def test_missing_description_raises():
    with pytest.raises(ValueError):
        _d("---\nname: ok\n---\n")


def test_unclosed_frontmatter_raises():
    with pytest.raises(ValueError):
        _d("---\nname: x\n")


def test_bom_and_crlf_normalized():
    d = _d("﻿---\r\nname: bom\r\ndescription: d\r\n---\r\n\r\nbody\r\n")
    assert d.name == "bom"
    assert d.system_prompt.strip() == "body"


def test_parse_file(tmp_path):
    p = tmp_path / "sub.md"
    p.write_text(_BASE, encoding="utf-8")
    d = parse_file(str(p), Source.USER)
    assert d.name == "explore"
    assert d.source is Source.USER
    assert d.file_path == str(p)


def test_name_regex():
    assert AGENT_NAME_REGEX.fullmatch("explore")
    assert AGENT_NAME_REGEX.fullmatch("Explore")
    assert AGENT_NAME_REGEX.fullmatch("plan-2_x")
    assert not AGENT_NAME_REGEX.fullmatch("")
    assert not AGENT_NAME_REGEX.fullmatch("1abc")


# ── ch14 isolation 字段 ──────────────────────────────────────────────────────


def test_isolation_worktree():
    d = _d("---\nname: wt\ndescription: d\nisolation: worktree\n---\n")
    assert d.isolation == "worktree"


def test_isolation_default_empty():
    d = _d("---\nname: a\ndescription: d\n---\n")
    assert d.isolation == ""


def test_invalid_isolation_fallback(capsys):
    d = _d("---\nname: bad\ndescription: d\nisolation: gibberish\n---\n")
    assert d.isolation == ""
    err = capsys.readouterr().err
    assert 'unknown isolation "gibberish"' in err
    assert "defaulting to" in err
