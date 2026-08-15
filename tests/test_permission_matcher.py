"""permission.Matcher 四种类型与 compile_matcher 工厂单测（ch12 T2）。"""

import pytest

from novacode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    NotMatcher,
    RegexMatcher,
    compile_matcher,
)


@pytest.mark.parametrize(
    ("desc", "target", "expected"),
    [
        # 精确整串
        ("=git status", "git status", True),
        ("=git status", "git status -s", False),
        ("=git status", "git push", False),
        # glob：命令整串通配
        ("git *", "git status", True),
        ("git *", "git status -s", True),
        ("git *", "npm i", False),
        ("git *", "git", False),  # 缺子命令不匹配
        # glob：文件路径分段
        ("**/*.py", "a/b.py", True),
        ("**/*.py", "a/b/c.py", True),
        ("**/*.py", "src", False),  # 无 .py 文件不匹配
        ("*.py", "app.py", True),
        ("*.py", "a/b.py", False),  # * 不跨段
        # 正则 search
        ("~^npm (install|test)$", "npm install", True),
        ("~^npm (install|test)$", "npm test", True),
        ("~^npm (install|test)$", "npm run dev", False),
        ("~^rm", "rm -rf .", True),
        # 反向 !=value
        ("!=foo", "foo", False),
        ("!=foo", "bar", True),
        # 反向 !~regex
        ("!~^rm", "rm -rf .", False),
        ("!~^rm", "ls -lh", True),
        # 反向 !glob（嵌套 not + glob）
        ("!git *", "git status", False),
        ("!git *", "npm install", True),
    ],
)
def test_compile_matcher_match(desc, target, expected):
    m = compile_matcher(desc, is_command=True)
    assert m.match(target) is expected


def test_compile_matcher_types():
    assert isinstance(compile_matcher("=foo", is_command=False), ExactMatcher)
    assert isinstance(compile_matcher("git *", is_command=False), GlobMatcher)
    assert isinstance(compile_matcher("~^rm", is_command=False), RegexMatcher)
    inner = compile_matcher("!=foo", is_command=False)
    assert isinstance(inner, NotMatcher)
    assert isinstance(inner.inner, ExactMatcher)


def test_compile_matcher_errors():
    with pytest.raises(ValueError):
        compile_matcher("", is_command=False)
    with pytest.raises(ValueError):
        compile_matcher("~[invalid", is_command=False)  # 正则编译失败
    with pytest.raises(ValueError):
        compile_matcher("!", is_command=False)  # 空 inner


def test_glob_matcher_path_and_command_semantics():
    """match_pattern 自判定：含 `/` 的目标走路径分段，否则命令整串通配。"""
    g = compile_matcher("**/*.py", is_command=False)
    assert g.match("a/b.py") and not g.match("docs/x.md")
    c = compile_matcher("git *", is_command=True)
    assert c.match("git status -s") and not c.match("npm i")


def test_str_roundtrip():
    assert str(compile_matcher("=foo", is_command=False)) == "=foo"
    assert str(compile_matcher("git *", is_command=False)) == "git *"
    assert str(compile_matcher("~^rm", is_command=False)) == "~^rm"
    assert str(compile_matcher("!=foo", is_command=False)) == "!=foo"
