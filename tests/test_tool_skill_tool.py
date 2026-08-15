"""Skill 专属工具（skill_tool）单测（ch11 T15）。"""

from __future__ import annotations

import asyncio
import sys

from novacode.tool.skill_tool import new_skill_tool


def _script(dir, code):
    p = dir / "s.py"
    p.write_text(code, encoding="utf-8")
    return p


def test_skill_tool_echo(tmp_path):
    """stdout 作结果文本（N12）。"""
    p = _script(tmp_path, "import sys; print('ok')")
    tool = new_skill_tool("my_tool", "d", {"type": "object"}, [sys.executable, str(p)], tmp_path)
    r = asyncio.run(tool.execute("{}"))
    assert r.is_error is False
    assert r.content == "ok"


def test_skill_tool_timeout(tmp_path):
    """超时 30s 返回错误（checklist：sleep 100 脚本）。"""
    p = _script(tmp_path, "import time; time.sleep(100)")
    tool = new_skill_tool("slow", "d", {"type": "object"}, [sys.executable, str(p)], tmp_path)
    r = asyncio.run(tool.execute("{}"))
    assert r.is_error and "超时" in r.content


def test_skill_tool_nonzero_exit(tmp_path):
    """returncode != 0 → 失败，stderr 并入结果（checklist）。"""
    p = _script(tmp_path, "import sys; print('boom'); sys.exit(3)")
    tool = new_skill_tool("bad", "d", {"type": "object"}, [sys.executable, str(p)], tmp_path)
    r = asyncio.run(tool.execute("{}"))
    assert r.is_error and "boom" in r.content


def test_skill_tool_missing_script(tmp_path):
    tool = new_skill_tool("nope", "d", {"type": "object"}, ["/no/such/script.py"], tmp_path)
    r = asyncio.run(tool.execute("{}"))
    assert r.is_error
