"""bash 工具单测（ch11 Windows 适配）：平台感知 description + 输出解码。"""

from __future__ import annotations

import asyncio

import pytest

from novacode.tool.bash import BashTool, _decode


def test_bash_description_win32(monkeypatch):
    """Windows 上 description 提示 AI 用 cmd 语法、勿用 bash 专属命令。"""
    monkeypatch.setattr("novacode.tool.bash.sys.platform", "win32")
    desc = BashTool().description()
    assert "cmd.exe" in desc
    assert "ls" in desc or "tail" in desc  # 明确点名不要用 bash 专属命令
    assert "findstr" in desc


def test_bash_description_posix(monkeypatch):
    """非 Windows 上 description 不含 cmd 平台提醒。"""
    monkeypatch.setattr("novacode.tool.bash.sys.platform", "linux")
    desc = BashTool().description()
    assert "cmd.exe" not in desc
    assert "shell" in desc


def test_decode_gbk_output(monkeypatch):
    """Windows 上 GBK 字节（系统消息）按 gbk 解码，不乱码。"""
    monkeypatch.setattr("novacode.tool.bash.sys.platform", "win32")
    gbk = "系统找不到指定的路径".encode("gbk")
    assert _decode(gbk) == "系统找不到指定的路径"


def test_decode_posix(monkeypatch):
    """非 Windows 上按 utf-8 解码。"""
    monkeypatch.setattr("novacode.tool.bash.sys.platform", "linux")
    assert _decode(b"hello") == "hello"


@pytest.mark.asyncio
async def test_bash_execute_echo():
    """命令执行基本路径：stdout 回传。"""
    tool = BashTool()
    r = await tool.execute('{"command": "echo ok"}')
    assert "ok" in r.content


def test_bash_execute_missing_command():
    tool = BashTool()
    r = asyncio.run(tool.execute("{}"))
    assert r.is_error and "缺少参数" in r.content
