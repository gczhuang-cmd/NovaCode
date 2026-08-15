"""MCP 连接管理器：并发连接、30s 超时、失败隔离、退出统一关闭。

**关键约束（mcp 2.0）**：stdio / Streamable HTTP transport 内部使用 anyio task
group，要求 async 上下文的 `__aenter__` 与 `__aexit__` 在**同一 task** 执行。
因此每个 server 的完整生命周期（进入 transport/session 上下文 → 握手 → 列工具 →
持有 → 关闭）由**一个常驻协程** `_run_session` 独占；`Manager.close()` 只触发各
协程的 exit_event 并等待它们自行收尾（各自的 `__aexit__` 都在原 task 内执行），
**绝不在新 task 里跨 task 关闭上下文**（否则 anyio 抛
`Attempted to exit cancel scope in a different task`）。

对外接口：
- `Manager`：持有成功会话与适配工具，`tools()` / `close()`。
- `new_manager(cfg, version) -> Manager`：并发连接所有 server，单 server 失败/超时
  只跳过它自身；阻塞直到全部尝试结束（F9）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .config import Config, ServerConfig
from .tool import McpTool, adapt_tool

# 连接 / 关闭超时（模块级非常量，便于单测临时改小；生产 30s / 5s，F9/F11 内置不可配）
connect_timeout: float = 30.0
close_timeout: float = 5.0


@dataclass
class _Session:
    name: str
    session: Any  # mcp.ClientSession（运行时类型；用 Any 避免模块顶部依赖 mcp）


class Manager:
    """成功会话 + 适配工具的统一持有者；close 时触发各生命周期协程自行收尾。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: list[_Session] = []
        self._tools: list[McpTool] = []
        self._tasks: dict[str, asyncio.Task] = {}  # server 名 → 生命周期协程
        self._ready: dict[str, asyncio.Event] = {}  # 握手完成信号（成功注册或失败）
        self._exit_events: dict[str, asyncio.Event] = {}  # close 触发信号

    def tools(self) -> list[McpTool]:
        """返回适配好的工具列表副本（防外部修改）。"""
        return list(self._tools)

    async def close(self) -> None:
        """触发所有协程退出并等待收尾；总超时 close_timeout 兜底，绝不阻塞退出（F11/N7）。"""
        for evt in self._exit_events.values():
            evt.set()
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks.values(), return_exceptions=True),
                timeout=close_timeout,
            )
        except TimeoutError:
            print(
                f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak",
                file=sys.stderr,
            )


async def new_manager(cfg: Config, version: str) -> Manager:
    """并发连接所有 server；阻塞直到每个 server 的握手阶段结束，然后按名排序。

    - 每个 server 一个常驻协程；成功者注册工具后进入持有态；
    - 整体握手窗口受 connect_timeout 约束（并发总时延上界 30s）；超时取消未就绪 server。
    """
    mgr = Manager()
    for name, srv in cfg.servers.items():
        mgr._ready[name] = asyncio.Event()
        mgr._tasks[name] = asyncio.create_task(_run_session(mgr, name, srv, version))
    try:
        await asyncio.wait_for(
            asyncio.gather(*[mgr._ready[name].wait() for name in cfg.servers]),
            timeout=connect_timeout,
        )
    except TimeoutError:
        for name, task in mgr._tasks.items():
            if not mgr._ready[name].is_set():
                task.cancel()
        print(
            f"[mcp] warn: connect timeout after {connect_timeout}s, some servers skipped",
            file=sys.stderr,
        )
    mgr._tools.sort(key=lambda t: t.name())
    return mgr


async def _run_session(mgr: Manager, name: str, srv: ServerConfig, version: str) -> None:
    """单个 server 的完整生命周期（常驻协程）：连接 + 握手 + 持有，直到 close。

    上下文进入 / 退出都在本协程内（同一 task），满足 anyio task group 约束；
    握手段（`_handshake`）受 connect_timeout 单独限时，持有段不限时。
    """
    try:
        async with _transport(srv) as transport:
            read, write = transport
            import mcp.types as mtypes
            from mcp import ClientSession

            async with ClientSession(
                read,
                write,
                client_info=mtypes.Implementation(name="novacode", version=version),
            ) as session:
                exit_event = asyncio.Event()
                await asyncio.wait_for(
                    _handshake(session, mgr, name, exit_event),
                    timeout=connect_timeout,
                )
                mgr._ready[name].set()  # 握手成功 → 通知 new_manager
                await exit_event.wait()  # 持有直到 close 触发
    except TimeoutError:
        print(
            f"[mcp] warn: connect server {name} timeout after {connect_timeout}s",
            file=sys.stderr,
        )
        mgr._ready[name].set()
    except asyncio.CancelledError:
        mgr._ready[name].set()
        raise
    except Exception as e:  # noqa: BLE001 失败隔离：只跳过该 server（F9/N1）
        print(f"[mcp] warn: connect server {name} failed: {e}", file=sys.stderr)
        mgr._ready[name].set()


@asynccontextmanager
async def _transport(srv: ServerConfig):
    """按类型构造 transport 异步上下文；enter/exit 都在调用协程（同一 task）内。"""
    if srv.type == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=srv.command,
            args=srv.args,
            env={**os.environ, **srv.env},  # 同名宿主变量被 env 覆盖（F4）
        )
        async with stdio_client(params) as transport:
            yield transport
    else:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        async with (
            httpx2.AsyncClient(headers=srv.headers or None) as http_client,
            streamable_http_client(srv.url, http_client=http_client) as streams,
        ):
            # 兼容 mcp SDK 版本差异：旧版 3 元组 (read, write, exit_stack)，新版 2 元组 (read, write)
            read, write = streams[0], streams[1]
            yield (read, write)


async def _handshake(session: Any, mgr: Manager, name: str, exit_event: asyncio.Event) -> None:
    """握手 + 列工具 + 适配 + 注册（F6/F9）。成功后把该 server 的 exit_event 挂进 Manager。"""
    await session.initialize()  # 握手
    listed = await session.list_tools()
    adapted: list[McpTool] = []
    for t in listed.tools:
        mt = adapt_tool(name, t, session)
        if mt is not None:
            adapted.append(mt)
    async with mgr._lock:
        mgr._sessions.append(_Session(name, session))
        mgr._tools.extend(adapted)
        mgr._exit_events[name] = exit_event
