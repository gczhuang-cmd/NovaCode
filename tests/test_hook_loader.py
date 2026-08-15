"""hook.loader 单测：双层加载、字段校验、错误跳过（ch12 T9）。

所有用例把用户级 home monkeypatch 到 tmp_path，避免污染真实 `~/.novacode/hooks.yaml`。
"""

import textwrap

from novacode.hook import Event
from novacode.hook.loader import load


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _home(monkeypatch, base):
    """把用户级 hooks.yaml 指向 base/home，避免读取真实用户配置。"""
    monkeypatch.setattr("novacode.hook.loader.Path.home", lambda: base / "home")


def test_load_valid_two_hooks(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    _write(
        tmp_path / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: block-write
            event: PreToolUse
            if:
              all_of:
                - field: tool_name
                  match: { type: exact, value: write_file }
            action:
              type: shell
              command: "echo blocked >&2; exit 2"
          - name: zh-cn
            event: SessionStart
            action:
              type: prompt
              text: "用 zh-CN"
    """,
    )
    engine = load(tmp_path)
    assert len(engine.rules) == 2
    assert engine.rules[0].name == "block-write"
    assert engine.rules[0].event is Event.PRE_TOOL_USE
    assert engine.rules[0].condition is not None
    assert engine.rules[1].action.prompt.text == "用 zh-CN"
    assert engine.sources == [str(tmp_path / ".novacode" / "hooks.yaml")]


def test_load_missing_file_no_error(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    engine = load(tmp_path)
    assert engine.rules == [] and engine.sources == []


def test_load_skips_unknown_event(tmp_path, monkeypatch, capsys):
    _home(monkeypatch, tmp_path)
    _write(
        tmp_path / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: bad-event
            event: UnknownEvent
            action: { type: shell, command: "echo x" }
          - name: good
            event: SessionStart
            action: { type: shell, command: "echo ok" }
    """,
    )
    engine = load(tmp_path)
    assert [r.name for r in engine.rules] == ["good"]
    assert 'unknown event "UnknownEvent"' in capsys.readouterr().err


def test_load_async_blocking_conflict(tmp_path, monkeypatch, capsys):
    _home(monkeypatch, tmp_path)
    _write(
        tmp_path / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: bad-async
            event: PreToolUse
            async: true
            action: { type: shell, command: "echo x" }
          - name: good
            event: SessionStart
            action: { type: shell, command: "echo ok" }
    """,
    )
    engine = load(tmp_path)
    assert [r.name for r in engine.rules] == ["good"]
    assert "async not allowed for blocking events" in capsys.readouterr().err


def test_load_all_of_any_of_both(tmp_path, monkeypatch, capsys):
    _home(monkeypatch, tmp_path)
    _write(
        tmp_path / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: both
            event: SessionStart
            if:
              all_of: [{ field: a, match: { type: exact, value: x } }]
              any_of: [{ field: b, match: { type: exact, value: y } }]
            action: { type: shell, command: "echo x" }
          - name: good
            event: SessionStart
            action: { type: shell, command: "echo ok" }
    """,
    )
    engine = load(tmp_path)
    assert [r.name for r in engine.rules] == ["good"]
    assert "exactly one of all_of/any_of" in capsys.readouterr().err


def test_load_cross_file_name_conflict(tmp_path, monkeypatch, capsys):
    _write(
        tmp_path / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: dup
            event: SessionStart
            action: { type: shell, command: "echo project" }
    """,
    )
    _write(
        tmp_path / "home" / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: dup
            event: SessionEnd
            action: { type: shell, command: "echo user" }
    """,
    )
    monkeypatch.setattr("novacode.hook.loader.Path.home", lambda: tmp_path / "home")
    engine = load(tmp_path)
    assert [r.name for r in engine.rules] == ["dup"]
    assert engine.rules[0].event is Event.SESSION_START  # 项目级保留、用户级跳过（F7）
    assert len(engine.sources) == 2


def test_load_invalid_regex_skips(tmp_path, monkeypatch, capsys):
    _home(monkeypatch, tmp_path)
    _write(
        tmp_path / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: bad-regex
            event: SessionStart
            if:
              all_of:
                - field: prompt
                  match: { type: regex, value: "(unclosed" }
            action: { type: shell, command: "echo x" }
          - name: good
            event: SessionStart
            action: { type: shell, command: "echo ok" }
    """,
    )
    engine = load(tmp_path)
    assert [r.name for r in engine.rules] == ["good"]
    assert "invalid regex" in capsys.readouterr().err


def test_load_timeout_parsing(tmp_path, monkeypatch, capsys):
    _home(monkeypatch, tmp_path)
    _write(
        tmp_path / ".novacode" / "hooks.yaml",
        """
        hooks:
          - name: timed
            event: SessionStart
            timeout: 5s
            action: { type: http, url: "http://x" }
          - name: defaulted
            event: SessionStart
            action: { type: shell, command: "echo x" }
          - name: bad-timeout
            event: SessionStart
            timeout: "abc"
            action: { type: shell, command: "echo x" }
    """,
    )
    engine = load(tmp_path)
    assert engine.rules[0].timeout_s == 5.0
    assert engine.rules[1].timeout_s == 30.0
    assert [r.name for r in engine.rules] == ["timed", "defaulted"]
    assert "invalid timeout" in capsys.readouterr().err


def test_load_yaml_broken_file_skipped(tmp_path, monkeypatch, capsys):
    _home(monkeypatch, tmp_path)
    _write(tmp_path / ".novacode" / "hooks.yaml", "hooks: [unclosed")
    engine = load(tmp_path)
    assert engine.rules == [] and engine.sources == []
    assert "yaml parse failed" in capsys.readouterr().err


def test_load_not_a_hooks_list(tmp_path, monkeypatch, capsys):
    _home(monkeypatch, tmp_path)
    _write(tmp_path / ".novacode" / "hooks.yaml", "something: else\n")
    engine = load(tmp_path)
    assert engine.rules == [] and engine.sources == []
    assert "hooks' list required" in capsys.readouterr().err
