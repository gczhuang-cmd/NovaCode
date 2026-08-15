"""hook.Executor 单测：shell exit2 / http block / prompt / subagent stub（ch12 T12）。

shell 命令用 `sys.executable` 构造，保证 Windows（cmd.exe）与 Linux（sh）都能跑。
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from novacode.hook import Event
from novacode.hook.executor import Executor
from novacode.hook.rule import (
    Action,
    ActionType,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)


def _py(cmd_body: str) -> str:
    """用当前解释器执行一段 python，构造跨平台 shell 命令串。"""
    return f'"{sys.executable}" -c "{cmd_body}"'


EXIT2 = _py("import sys; print('denied', file=sys.stderr); sys.exit(2)")
EXIT0 = _py("import sys; sys.exit(0)")
EXIT1 = _py("import sys; sys.exit(1)")
SLEEP = _py("import time; time.sleep(2)")


def _shell_rule(command, timeout=30.0):
    return Rule(
        name="t",
        event=Event.STOP,
        action=Action(type=ActionType.SHELL, shell=ShellAction(command=command)),
        timeout_s=timeout,
    )


def _http_rule(url, method="POST", body=None, timeout=30.0):
    return Rule(
        name="t",
        event=Event.STOP,
        action=Action(type=ActionType.HTTP, http=HttpAction(url=url, method=method, body=body)),
        timeout_s=timeout,
    )


class _Handler(BaseHTTPRequestHandler):
    responses: ClassVar[dict] = {}
    received: ClassVar[list] = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        type(self).received.append((self.path, body))
        status, resp = type(self).responses.get(self.path, (200, b"{}"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    _Handler.received = []
    _Handler.responses = {}
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


# ── shell ─────────────────────────────────────────────────────────────────────


async def test_shell_exit2_blocked():
    ex = Executor()
    r = await ex.run(_shell_rule(EXIT2), {"event": "PreToolUse"}, blocking=True)
    assert r.blocked and r.reason == "denied" and r.err is None


async def test_shell_exit0_allowed():
    ex = Executor()
    r = await ex.run(_shell_rule(EXIT0), {}, blocking=True)
    assert not r.blocked and r.err is None


async def test_shell_exit1_failure_not_block():
    ex = Executor()
    r = await ex.run(_shell_rule(EXIT1), {}, blocking=True)
    assert r.err is not None and not r.blocked


async def test_shell_exit2_non_blocking_not_block():
    """非拦截上下文（blocking=False）：exit 2 也按失败处理，不表达拦截。"""
    ex = Executor()
    r = await ex.run(_shell_rule(EXIT2), {}, blocking=False)
    assert r.err is not None and not r.blocked


async def test_shell_stdin_json_sorted(tmp_path):
    out = tmp_path / "out.json"
    cmd = _py(f"import sys; open(r'{out}','wb').write(sys.stdin.buffer.read())")
    ex = Executor()
    r = await ex.run(_shell_rule(cmd), {"b": 1, "a": 2}, blocking=False)
    assert r.err is None
    raw = out.read_bytes()
    data = json.loads(raw)
    assert data == {"a": 2, "b": 1}
    assert list(data.keys()) == ["a", "b"]  # N6：key 字典序


async def test_shell_timeout():
    ex = Executor()
    r = await ex.run(_shell_rule(SLEEP, timeout=0.1), {}, blocking=False)
    assert isinstance(r.err, TimeoutError) and not r.blocked


# ── prompt ────────────────────────────────────────────────────────────────────


async def test_prompt_action():
    ex = Executor()
    rule = Rule(
        name="p",
        event=Event.SESSION_START,
        action=Action(type=ActionType.PROMPT, prompt=PromptAction(text="用 zh-CN")),
    )
    r = await ex.run(rule, {}, blocking=False)
    assert r.prompt == "用 zh-CN" and r.err is None and not r.blocked


# ── http ──────────────────────────────────────────────────────────────────────


async def test_http_block(http_server):
    _Handler.responses = {"/check": (200, b'{"decision":"block","reason":"network policy"}')}
    ex = Executor()
    r = await ex.run(
        _http_rule(f"http://127.0.0.1:{http_server}/check"), {"event": "PreToolUse"}, blocking=True
    )
    assert r.blocked and r.reason == "network policy"


async def test_http_non_block_response_allows(http_server):
    _Handler.responses = {"/ok": (200, b'{"decision":"allow"}')}
    ex = Executor()
    r = await ex.run(_http_rule(f"http://127.0.0.1:{http_server}/ok"), {}, blocking=True)
    assert not r.blocked and r.err is None


async def test_http_5xx_error(http_server):
    _Handler.responses = {"/boom": (500, b"oops")}
    ex = Executor()
    r = await ex.run(_http_rule(f"http://127.0.0.1:{http_server}/boom"), {}, blocking=True)
    assert r.err is not None and not r.blocked


async def test_http_bad_json_body_error(http_server):
    _Handler.responses = {"/bad": (200, b"not-json")}
    ex = Executor()
    r = await ex.run(_http_rule(f"http://127.0.0.1:{http_server}/bad"), {}, blocking=True)
    assert r.err is not None and not r.blocked


async def test_http_template_body(http_server):
    ex = Executor()
    rule = _http_rule(f"http://127.0.0.1:{http_server}/tpl", body="{event}|{prompt}")
    await ex.run(rule, {"event": "Stop", "prompt": "hi"}, blocking=False)
    _, body = _Handler.received[-1]
    assert body == b"Stop|hi"


async def test_http_default_json_body(http_server):
    ex = Executor()
    await ex.run(
        _http_rule(f"http://127.0.0.1:{http_server}/json"), {"b": 1, "a": 2}, blocking=False
    )
    _, body = _Handler.received[-1]
    data = json.loads(body)
    assert data == {"a": 2, "b": 1}


# ── subagent stub ─────────────────────────────────────────────────────────────


async def test_subagent_stub_logs(capsys):
    ex = Executor()
    rule = Rule(
        name="s",
        event=Event.SESSION_START,
        action=Action(
            type=ActionType.SUBAGENT, subagent=SubagentAction(agent_name="foo", prompt="test")
        ),
    )
    r = await ex.run(rule, {}, blocking=False)
    assert r.err is None and not r.blocked and r.prompt == ""
    assert "[hook subagent] not yet implemented, skipped: foo" in capsys.readouterr().err
