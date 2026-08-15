"""规则解析与匹配单测：parse_rule（四种匹配前缀）、match_pattern、deny 优先（ch12 T5）。"""

import pytest

from novacode.permission import Decision
from novacode.permission.matcher import ExactMatcher, NotMatcher, RegexMatcher
from novacode.permission.rule import Rule, RuleSet, match_pattern, parse_rule


def _rule(spec: str, *, allow: bool) -> Rule:
    r, ok = parse_rule(spec)
    assert ok, spec
    r.allow = allow
    return r


def test_parse_rule_with_pattern():
    r, ok = parse_rule("Bash(git *)")
    assert ok and r.tool == "Bash" and r.matcher is not None and r.raw == "git *"


def test_parse_rule_bare_tool():
    r, ok = parse_rule("Read")
    assert ok and r.tool == "Read" and r.matcher is None  # 全匹配


def test_parse_rule_invalid():
    for bad in ("", "(", "Bash(unclosed", "Bash(git *) extra"):
        _, ok = parse_rule(bad)
        assert not ok


def test_parse_rule_nested_brackets_now_legal():
    """ch12：模式段允许嵌套括号（正则语法 `(install|test)`），不再视为非法。"""
    r, ok = parse_rule("Read(paren(inner))")
    assert ok and r.raw == "paren(inner)"


def test_parse_rule_lowercase_tool_ok():
    """小写工具名格式合法（映射到友好名发生在 settings 层）。"""
    r, ok = parse_rule("bash(git)")
    assert ok and r.tool == "bash" and r.matcher is not None


def test_parse_rule_exact():
    r, ok = parse_rule("Bash(=git status)")
    assert ok and isinstance(r.matcher, ExactMatcher)
    assert r.matcher.match("git status") and not r.matcher.match("git status -s")


def test_parse_rule_regex_nested_parens():
    r, ok = parse_rule("Bash(~^npm (install|test)$)")
    assert ok and isinstance(r.matcher, RegexMatcher)
    assert r.matcher.match("npm install") and not r.matcher.match("npm run dev")


def test_parse_rule_not_regex():
    r, ok = parse_rule("Bash(!~^rm)")
    assert ok and isinstance(r.matcher, NotMatcher)
    assert r.matcher.match("ls -lh") and not r.matcher.match("rm -rf .")


def test_parse_rule_invalid_prefix_carries_error():
    r, ok = parse_rule("Bash(~[invalid)")
    assert not ok and r.error != "" and r.raw == "~[invalid"


def test_parse_rule_path_glob_vs_command_glob():
    w, ok = parse_rule("Write(**/*.py)")
    assert ok and w.matcher is not None and w.matcher.match("a/b.py")
    b, ok = parse_rule("Bash(git *)")
    assert ok and b.matcher is not None and b.matcher.match("git status -s")


@pytest.mark.parametrize(
    ("pattern", "target", "expected"),
    [
        ("", "anything", True),  # 空模式恒匹配
        ("git status", "git status", True),
        ("git status", "git push", False),
        ("git *", "git status", True),
        ("git *", "git push --force", True),
        ("git *", "npm i", False),
        ("git *", "git", False),  # 缺子命令不匹配
        ("src/**", "src/a/b.py", True),
        ("src/**", "src", True),  # ** 可匹配零段
        ("src/**", "docs/x", False),
        ("**/*.py", "a/b/c.py", True),
        ("*.py", "app.py", True),
        ("*.py", "a/b.py", False),  # * 不跨段
    ],
)
def test_match_pattern(pattern, target, expected):
    assert match_pattern(pattern, target) is expected


def test_ruleset_deny_precedes_allow():
    rs = RuleSet()
    rs.allow.append(_rule("Bash(git *)", allow=True))
    rs.deny.append(_rule("Bash(git push)", allow=False))
    d, hit = rs.match("Bash", "git push")
    assert hit and d == Decision.DENY
    d, hit = rs.match("Bash", "git status")
    assert hit and d == Decision.ALLOW


def test_ruleset_no_match():
    rs = RuleSet()
    d, hit = rs.match("Bash", "npm i")
    assert not hit and d == Decision.ALLOW


def test_ruleset_bare_tool_matches_all():
    rs = RuleSet()
    rs.deny.append(_rule("Bash", allow=False))
    d, hit = rs.match("Bash", "anything at all")
    assert hit and d == Decision.DENY
