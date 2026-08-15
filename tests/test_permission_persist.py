"""永久放行规则写入单测：落盘、重载生效、幂等、精确不泛化（T7 / AC10）。"""

import json
from pathlib import Path

from novacode.llm import ToolCall
from novacode.permission import Decision, Mode, new_engine
from novacode.permission.engine import Engine
from novacode.permission.persist import escape_glob, persist_local_allow, rule_for


def _call(name: str, **kw) -> ToolCall:
    return ToolCall(id="id", name=name, input=json.dumps(kw))


def _engine(tmp_path) -> Engine:
    return Engine(
        root=str(tmp_path.resolve()),
        local_path=str(tmp_path / ".novacode" / "settings.local.yaml"),
    )


def test_persist_bash_rule_written_and_reload(tmp_path):
    e = _engine(tmp_path)
    persist_local_allow(e, _call("bash", command="git status"))
    p = Path(e.local_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "Bash(git status)" in text
    # 重载新引擎 → 同调用判放行
    engine2, _ = new_engine(str(tmp_path))
    d, _ = engine2.check(Mode.DEFAULT, _call("bash", command="git status"), read_only=False)
    assert d == Decision.ALLOW


def test_persist_file_relative_rule(tmp_path):
    e = _engine(tmp_path)
    (tmp_path / "src").mkdir()
    persist_local_allow(e, _call("write_file", path="src/a.py", content="x"))
    text = Path(e.local_path).read_text(encoding="utf-8")
    assert "Write(src/a.py)" in text
    engine2, _ = new_engine(str(tmp_path))
    d, _ = engine2.check(
        Mode.DEFAULT, _call("write_file", path="src/a.py", content="x"), read_only=False
    )
    assert d == Decision.ALLOW


def test_persist_idempotent(tmp_path):
    e = _engine(tmp_path)
    for _ in range(2):
        persist_local_allow(e, _call("bash", command="pytest -q"))
    text = Path(e.local_path).read_text(encoding="utf-8")
    assert text.count("Bash(pytest -q)") == 1  # 不重复写入


def test_persist_rule_not_generalized(tmp_path):
    """精确规则转义 glob 元字符，防止被通配泛化。"""
    e = _engine(tmp_path)
    persist_local_allow(e, _call("bash", command="echo a*b"))
    text = Path(e.local_path).read_text(encoding="utf-8")
    assert "Bash(echo a\\*b)" in text
    # 重载后：精确规则只放行原命令串，不放行其他 echo aXXb
    engine2, _ = new_engine(str(tmp_path))
    d, _ = engine2.check(Mode.DEFAULT, _call("bash", command="echo axb"), read_only=False)
    assert d == Decision.ASK  # 未命中精确规则 → 模式兜底 Ask


def test_escape_glob():
    assert escape_glob("git *") == "git \\*"
    assert escape_glob("a?b[c]") == "a\\?b\\[c\\]"
    assert escape_glob("plain") == "plain"


def test_rule_for_unparseable_fails(tmp_path):
    e = _engine(tmp_path)
    bad = ToolCall(id="id", name="read_file", input="not-json")
    r, text, ok = rule_for(e.root, bad)
    assert not ok and r.tool == "" and text == ""


def test_persist_mcp_tool_rule_written_and_reload(tmp_path):
    """ch07：MCP 工具永久放行 → 写 `mcp__<server>__<tool>` 工具级精确规则，重载后放行。"""
    e = _engine(tmp_path)
    call = ToolCall(id="id", name="mcp__demo__echo", input=json.dumps({"text": "hi"}))
    persist_local_allow(e, call)
    p = Path(e.local_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "mcp__demo__echo" in text
    # 重载新引擎 → 同工具调用判放行（default 不再 Ask）
    engine2, _ = new_engine(str(tmp_path))
    d, _ = engine2.check(Mode.DEFAULT, call, read_only=False)
    assert d == Decision.ALLOW
    # 精确匹配不泛化：其它 MCP 工具不受影响
    other = ToolCall(id="id2", name="mcp__demo__add", input=json.dumps({"a": 1, "b": 2}))
    d2, _ = engine2.check(Mode.DEFAULT, other, read_only=False)
    assert d2 == Decision.ASK
