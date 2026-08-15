"""MCP 连接管理器测试：连接成功/失败/超时、close 兜底、共享状态排序（AC8/AC10/F9/F11/N7）。"""

import asyncio
import sys
from contextlib import asynccontextmanager

import novacode.mcp.manager as mgr_mod
from novacode.mcp import Config, ServerConfig
from novacode.mcp.tool import McpTool


def _tool(name: str) -> McpTool:
    return McpTool(
        full_name=name,
        remote_name="t",
        description="",
        parameters={},
        read_only=False,
        caller=None,
    )


async def test_empty_cfg_tools_empty_and_close_immediate():
    mgr = await mgr_mod.new_manager(Config(), "v")
    assert mgr.tools() == []
    await mgr.close()  # 立即返回，不抛错


async def test_connect_failure_isolation(monkeypatch, capsys):
    """坏 server 只跳过自身，好 server 工具照常注册（AC8/F9/N1）。"""
    cfg = Config()
    cfg.servers["bad"] = ServerConfig(type="stdio", command="x")
    cfg.servers["good"] = ServerConfig(type="stdio", command="x")

    async def fake_run_session(mgr, name, srv, version):
        if name == "bad":
            print(f"[mcp] warn: connect server {name} failed: boom", file=sys.stderr)
            mgr._ready[name].set()
            return
        exit_event = asyncio.Event()
        async with mgr._lock:
            mgr._tools.append(_tool(f"mcp__{name}__t"))
            mgr._exit_events[name] = exit_event
        mgr._ready[name].set()
        await exit_event.wait()

    monkeypatch.setattr(mgr_mod, "_run_session", fake_run_session)
    mgr = await mgr_mod.new_manager(cfg, "v")
    try:
        assert [t.name() for t in mgr.tools()] == ["mcp__good__t"]
        assert "bad" in capsys.readouterr().err
    finally:
        await mgr.close()


async def test_connect_timeout_isolation(monkeypatch, capsys):
    """连接卡住的 server 在超时窗口后跳过，启动不阻塞（AC8/F9/N1）。"""
    cfg = Config()
    cfg.servers["hang"] = ServerConfig(type="stdio", command="x")

    async def hang_run_session(mgr, name, srv, version):
        await asyncio.Event().wait()  # 永不 set ready → 触发整体超时兜底

    monkeypatch.setattr(mgr_mod, "_run_session", hang_run_session)
    monkeypatch.setattr(mgr_mod, "connect_timeout", 0.2)
    mgr = await mgr_mod.new_manager(cfg, "v")
    try:
        assert mgr.tools() == []
        assert "timeout" in capsys.readouterr().err
    finally:
        await mgr.close()


async def test_close_timeout_guard(monkeypatch, capsys):
    """某会话关闭卡住时 close 在兜底时间内返回、不阻塞（AC10/N7）。"""
    cfg = Config()
    cfg.servers["stuck"] = ServerConfig(type="stdio", command="x")

    async def stuck_run_session(mgr, name, srv, version):
        exit_event = asyncio.Event()
        async with mgr._lock:
            mgr._exit_events[name] = exit_event
        mgr._ready[name].set()
        await exit_event.wait()
        await asyncio.Event().wait()  # 收到退出信号后仍卡住（模拟 __aexit__ 阻塞）

    monkeypatch.setattr(mgr_mod, "_run_session", stuck_run_session)
    monkeypatch.setattr(mgr_mod, "close_timeout", 0.2)
    mgr = await mgr_mod.new_manager(cfg, "v")
    await mgr.close()  # ~0.2s 内返回
    assert "close timeout" in capsys.readouterr().err


async def test_tools_sorted_by_full_name(monkeypatch):
    """工具列表按 full_name 稳定排序，而非 task 完成顺序（并发安全）。"""
    cfg = Config()
    cfg.servers["z"] = ServerConfig(type="stdio", command="x")
    cfg.servers["a"] = ServerConfig(type="stdio", command="x")

    async def fake_run_session(mgr, name, srv, version):
        await asyncio.sleep(0.01 if name == "a" else 0)  # 让 a 后注册，验证排序覆盖完成顺序
        exit_event = asyncio.Event()
        async with mgr._lock:
            mgr._tools.append(_tool(f"mcp__{name}__t"))
            mgr._exit_events[name] = exit_event
        mgr._ready[name].set()
        await exit_event.wait()

    monkeypatch.setattr(mgr_mod, "_run_session", fake_run_session)
    mgr = await mgr_mod.new_manager(cfg, "v")
    try:
        names = [t.name() for t in mgr.tools()]
        assert names == sorted(names)
    finally:
        await mgr.close()


async def test_http_transport_passes_headers_and_url(monkeypatch):
    """http 类型把 headers 注入 httpx2.AsyncClient、url 传给 streamable_http_client（AC5/N6）。"""
    import httpx2

    captured: dict = {}

    class FakeAC:
        def __init__(self, headers=None):
            captured["headers"] = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    @asynccontextmanager
    async def fake_streamable(url, http_client):
        captured["url"] = url
        captured["http_client"] = http_client
        yield (object(), object())

    monkeypatch.setattr(httpx2, "AsyncClient", FakeAC)
    monkeypatch.setattr("mcp.client.streamable_http.streamable_http_client", fake_streamable)
    srv = ServerConfig(
        type="http",
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer x"},
    )
    async with mgr_mod._transport(srv):
        pass
    assert captured["url"] == "https://example.com/mcp"
    assert captured["headers"] == {"Authorization": "Bearer x"}
    assert captured["http_client"] is not None


async def test_real_connect_missing_command(capsys):
    """真实 stdio_client 对不存在的 command 快速失败、只告警不阻塞（AC4/N1）。"""
    cfg = Config()
    cfg.servers["bad"] = ServerConfig(type="stdio", command="/no/such/bin")
    mgr = await mgr_mod.new_manager(cfg, "v")
    try:
        assert mgr.tools() == []
        assert "bad" in capsys.readouterr().err
    finally:
        await mgr.close()
