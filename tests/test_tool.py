"""工具系统单测：注册中心 + 6 个核心工具。"""

import json

import pytest

from novacode.tool import Registry, new_default_registry
from novacode.tool.ctx import with_cwd  # ch14：ctx cwd 注入测试


def _args(**kw: object) -> str:
    """构造工具调用参数 JSON（json.dumps 正确转义路径反斜杠）。"""
    return json.dumps(kw)


def test_registry_count():
    """ch10 T0c：count() 返回已注册工具数（O(1)），随注册递增。"""
    r = Registry()
    assert r.count() == 0
    r.register(new_default_registry().get("read_file"))
    assert r.count() == 1


@pytest.mark.asyncio
async def test_registry_definitions_ordered():
    """AC1：注册中心导出恰好 6 条定义且按注册顺序，按名可查/未命中。"""
    r = new_default_registry()
    defs = r.definitions()
    assert [d.name for d in defs] == [
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "glob",
        "grep",
    ]
    assert r.get("read_file") is not None
    assert r.get("nope") is None


@pytest.mark.asyncio
async def test_registry_unknown_tool():
    """F9：未知工具返回结构化错误而非崩溃。"""
    r = Registry()
    res = await r.execute("nope", "{}")
    assert res.is_error
    assert "未知工具" in res.content


@pytest.mark.asyncio
async def test_read_file_exists_and_missing(tmp_path):
    """AC2：读存在文件带行号；读不存在文件返回结构化错误。"""
    p = tmp_path / "a.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")
    r = new_default_registry()
    res = await r.execute("read_file", _args(path=str(p)))
    assert not res.is_error
    assert "     1\thello" in res.content
    res = await r.execute("read_file", _args(path=str(tmp_path / "no_such.txt")))
    assert res.is_error


@pytest.mark.asyncio
async def test_read_file_directory_is_error(tmp_path):
    """AC2：读目录返回结构化错误。"""
    r = new_default_registry()
    res = await r.execute("read_file", _args(path=str(tmp_path)))
    assert res.is_error
    assert "目录" in res.content


@pytest.mark.asyncio
async def test_write_file_creates_nested(tmp_path):
    """AC3：写文件创建/覆盖，父目录自动创建。"""
    target = tmp_path / "a" / "b" / "c.txt"
    r = new_default_registry()
    res = await r.execute("write_file", _args(path=str(target), content="hi"))
    assert not res.is_error
    assert target.read_text(encoding="utf-8") == "hi"
    res = await r.execute("write_file", _args(path=str(target), content="hi2"))
    assert not res.is_error
    assert target.read_text(encoding="utf-8") == "hi2"


@pytest.mark.asyncio
async def test_edit_file_three_cases(tmp_path):
    """AC4：唯一替换成功；0 处与 >1 处返回可区分错误（含匹配数）。"""
    p = tmp_path / "e.txt"
    p.write_text("aaa bbb aaa\n", encoding="utf-8")
    r = new_default_registry()
    # 0 处
    res = await r.execute("edit_file", _args(path=str(p), old_string="zzz", new_string="x"))
    assert res.is_error and "未找到匹配" in res.content
    # >1 处
    res = await r.execute("edit_file", _args(path=str(p), old_string="aaa", new_string="x"))
    assert res.is_error and "匹配到 2 处" in res.content
    # 唯一
    res = await r.execute("edit_file", _args(path=str(p), old_string="bbb", new_string="CCC"))
    assert not res.is_error
    assert p.read_text(encoding="utf-8") == "aaa CCC aaa\n"


@pytest.mark.asyncio
async def test_bash_echo_and_timeout():
    """AC5：echo 返回输出；注入极短超时跑 sleep 得超时 is_error。"""
    r = new_default_registry()
    res = await r.execute("bash", _args(command="echo hi"))
    assert not res.is_error
    assert "hi" in res.content
    assert "exit_code: 0" in res.content
    res = await r.execute(
        "bash", _args(command='python -c "import time; time.sleep(5)"'), timeout=0.1
    )
    assert res.is_error
    assert "超时" in res.content


@pytest.mark.asyncio
async def test_bash_nonzero_exit_is_result():
    """F9：非零退出不设 is_error，按结果回灌由模型判断。"""
    r = new_default_registry()
    res = await r.execute("bash", _args(command="exit 3"))
    assert not res.is_error
    assert "exit_code: 3" in res.content


@pytest.mark.asyncio
async def test_glob_matches(tmp_path):
    """AC6：glob 按模式列出匹配文件。"""
    (tmp_path / "x.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.py").write_text("", encoding="utf-8")
    r = new_default_registry()
    res = await r.execute("glob", _args(pattern="**/*.py", path=str(tmp_path)))
    assert not res.is_error
    assert "x.py" in res.content and "y.py" in res.content


@pytest.mark.asyncio
async def test_glob_no_match_is_plain_result(tmp_path):
    """AC6：无匹配返回说明（非 is_error）。"""
    r = new_default_registry()
    res = await r.execute("glob", _args(pattern="**/*.zzz", path=str(tmp_path)))
    assert not res.is_error
    assert "无匹配" in res.content


@pytest.mark.asyncio
async def test_grep_hits(tmp_path):
    """AC6：grep 返回 file:line:content 命中。"""
    p = tmp_path / "g.txt"
    p.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    r = new_default_registry()
    res = await r.execute("grep", _args(pattern="alpha", path=str(tmp_path)))
    assert not res.is_error
    assert "g.txt:1:alpha" in res.content
    assert "g.txt:3:alpha" in res.content


@pytest.mark.asyncio
async def test_grep_bad_regex():
    """F9/N4：正则非法返回结构化错误。"""
    r = new_default_registry()
    res = await r.execute("grep", _args(pattern="(["))
    assert res.is_error
    assert "正则非法" in res.content


@pytest.mark.asyncio
async def test_grep_no_hit_is_plain_result(tmp_path):
    """AC6：无命中返回说明（非 is_error）。"""
    p = tmp_path / "g.txt"
    p.write_text("alpha\n", encoding="utf-8")
    r = new_default_registry()
    res = await r.execute("grep", _args(pattern="zzz", path=str(tmp_path)))
    assert not res.is_error
    assert "无命中" in res.content


# ── ch14 ctx cwd 注入（spec AC13/AC14）───────────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_with_ctx_cwd(tmp_path):
    """ctx cwd 注入后，相对 path 在 cwd 内解析。"""
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    r = new_default_registry()
    with with_cwd(str(tmp_path)):
        res = await r.execute("read_file", _args(path="a.txt"))
    assert not res.is_error
    assert "hello" in res.content


@pytest.mark.asyncio
async def test_write_file_with_ctx_cwd(tmp_path):
    r = new_default_registry()
    with with_cwd(str(tmp_path)):
        res = await r.execute("write_file", _args(path="nested/x.txt", content="hi"))
    assert not res.is_error
    assert (tmp_path / "nested" / "x.txt").read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_edit_file_with_ctx_cwd(tmp_path):
    (tmp_path / "e.txt").write_text("aaa\n", encoding="utf-8")
    r = new_default_registry()
    with with_cwd(str(tmp_path)):
        res = await r.execute("edit_file", _args(path="e.txt", old_string="aaa", new_string="bbb"))
    assert not res.is_error
    assert (tmp_path / "e.txt").read_text(encoding="utf-8") == "bbb\n"


@pytest.mark.asyncio
async def test_glob_with_ctx_cwd(tmp_path):
    (tmp_path / "x.py").write_text("", encoding="utf-8")
    r = new_default_registry()
    with with_cwd(str(tmp_path)):
        res = await r.execute("glob", _args(pattern="*.py"))
    assert not res.is_error
    assert "x.py" in res.content


@pytest.mark.asyncio
async def test_grep_with_ctx_cwd(tmp_path):
    (tmp_path / "g.txt").write_text("alpha\n", encoding="utf-8")
    r = new_default_registry()
    with with_cwd(str(tmp_path)):
        res = await r.execute("grep", _args(pattern="alpha"))
    assert not res.is_error
    assert "g.txt:1:alpha" in res.content


@pytest.mark.asyncio
async def test_bash_with_ctx_cwd(tmp_path):
    """bash 子进程 cwd 参数用 ctx cwd（进程 cwd 不受影响）。"""
    r = new_default_registry()
    with with_cwd(str(tmp_path)):
        res = await r.execute("bash", _args(command='python -c "import os; print(os.getcwd())"'))
    assert not res.is_error
    import os as _os

    assert _os.path.normcase(str(tmp_path)) in _os.path.normcase(res.content)
