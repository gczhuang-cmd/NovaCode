"""Skill fork 与 Agent 工具 Fork 共用的启动函数（ch13 T28，spec F33）。

`launch_fork` 构造 Fork 子 Agent 并前台同步跑 `run_to_completion`，返回 final_text。
Skill fork（skills/executor.py）与 Agent 工具 Fork 分支共用这段 wiring。
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass

from ..compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from ..llm import Provider
from ..permission import Engine, Mode
from ..tool import Registry


@dataclass
class ForkLaunchOpts:
    """launch_fork 入参（skills 已装填 conv + 可能的 model 切换）。"""

    allowed_tools: list[str]
    conv: object  # Conversation（已含任务文本）
    provider: Provider  # llm.Provider
    registry: Registry
    engine: Engine
    version: str
    hook_engine: object | None = None
    context_window: int = 200000


async def launch_fork(opts: ForkLaunchOpts, usage_sink: dict | None = None) -> str:
    """构造 Fork 子 Agent 并前台同步跑 run_to_completion，返回 final_text。

    - 权限模式固定 Mode.BYPASS（继承 skills fork 现状：allowed_tools 已收窄，
      黑名单/沙箱仍拦，无 UI 审批卡死风险）；
    - conv 已装填任务文本，`run_to_completion` 传 task="" 避免重复 add_user；
    - usage_sink（可选，`{"input":0,"output":0}` 会被累加）：fork 用量计入主会话
      （状态栏可见 + 压缩感知，N6）。
    """
    from ..agent import Agent
    from ..agent.runtime import SessionRuntime

    runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(tempfile.gettempdir()),
        context_window=opts.context_window,
    )
    sub = Agent(
        opts.provider,
        opts.registry,
        opts.version,
        opts.engine,
        runtime=runtime,
        allowed_tools=opts.allowed_tools,
        permission_mode=Mode.BYPASS,
        hook_engine=opts.hook_engine,
    )
    # mypy 收窄：usage_sink 为空时用本地空 dict（对调用方无影响）
    sink: dict = usage_sink if usage_sink is not None else {}
    events: asyncio.Queue | None = asyncio.Queue(maxsize=64) if sink else None
    collector: asyncio.Task | None = None
    if events is not None:

        async def _collect() -> None:
            try:
                while True:
                    item = await events.get()
                    if item is None:
                        break
                    if hasattr(item, "input") and hasattr(item, "output"):
                        sink["input"] += item.input
                        sink["output"] += item.output
            except asyncio.CancelledError:
                pass

        collector = asyncio.create_task(_collect())
    try:
        return await sub.run_to_completion(opts.conv, "", events=events)  # type: ignore[attr-defined]
    finally:
        if collector is not None and events is not None:
            # 先发 None sentinel 让 collector 自然消费完再退出，避免 cancel 竞态丢 usage
            await events.put(None)
            await collector
