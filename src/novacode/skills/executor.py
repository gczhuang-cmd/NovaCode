"""Skill 执行器：inline / fork 分发（ch11 T19）。

- inline：把渲染后的 SOP 作为 user 消息注入主对话并触发回合（F27）；
- fork：起子 Agent 跑一轮，final_text 写回主对话（F28/N6/N7）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

from ..conversation import Conversation
from ..llm import new_provider
from .render import render_body

logger = logging.getLogger(__name__)

# fork 子 Agent 总超时（N7：超时返回 [skill failed: ...]，不卡主对话）
FORK_TIMEOUT = 180.0


class Executor:
    """持有 catalog/active/registry/provider 等，执行 Skill。"""

    def __init__(
        self,
        catalog,
        active,
        registry,
        provider,
        provider_cfg,
        engine,
        version: str,
        runtime,
    ) -> None:
        self._catalog = catalog
        self._active = active
        self._registry = registry
        self._provider = provider
        self._provider_cfg = provider_cfg  # fork 切 model 用（F8）
        self._engine = engine
        self._version = version
        self._runtime = runtime

    async def execute(self, ctx, name: str, args: str) -> None:
        """执行一个 Skill（F26）：Skill 命令 handler 与显式调用共用入口。"""
        skill = self._catalog.get(name)
        if skill is None:
            ctx.console.print(f"[dim]skill not found: {name}[/]")
            return
        rendered = render_body(skill, args)
        if not skill.meta.is_fork():
            # inline：注入 user 消息并触发回合（F27，等价 /do 路径）
            ctx.conv.add_user(rendered)
            await ctx.run_callback()
        else:
            await self._run_fork(ctx, skill, rendered)

    async def _run_fork(self, ctx, skill, rendered: str) -> None:
        """fork 分支：子 Agent 跑完，final_text 写回主对话（N7 失败兜底）。"""
        try:
            final = await self._run_subagent(ctx, skill, rendered)
        except asyncio.CancelledError:
            final = f"[skill {skill.meta.name} failed: cancelled]"
        except Exception as e:  # noqa: BLE001 子 Agent 任何错误不卡主对话
            logger.warning("fork skill %s failed: %s", skill.meta.name, e)
            final = f"[skill {skill.meta.name} failed: {e}]"
        ctx.conv.add_assistant(final)

    async def _run_subagent(self, ctx, skill, rendered: str) -> str:
        """构造子 Agent 并跑一轮，返回末尾 assistant 文本（F28；ch13 复用 launch_fork）。"""
        if skill.meta.fork_context in ("recent", "full"):
            if skill.meta.fork_context == "full":
                logger.warning("fork_context=full 本期与 recent 等价处理，留待 ch12")
            recent = ctx.conv.messages()[-5:]
            fork_conv = Conversation.from_messages(recent)  # 无回调，不写盘
            fork_conv.add_user(rendered)
        else:
            fork_conv = Conversation()
            fork_conv.add_user(rendered)
        provider = self._provider
        if skill.meta.model:
            cfg = dataclasses.replace(self._provider_cfg, model=skill.meta.model)
            provider = new_provider(cfg)  # F8：Skill 指定模型时切换
        # 函数内延迟导入：避免 agent → skills → executor → agent 循环导入
        from ..agent.launch import ForkLaunchOpts, launch_fork

        # 子 Agent context_window 继承主对话配置（压缩阈值一致，避免频繁压缩）
        main_win = getattr(ctx.runtime, "context_window", None) if ctx.runtime is not None else None
        usage_sink = {"input": 0, "output": 0}
        try:
            final = await asyncio.wait_for(
                launch_fork(
                    ForkLaunchOpts(
                        allowed_tools=skill.meta.allowed_tools,  # F28：白名单收窄子 Agent 工具集
                        conv=fork_conv,
                        provider=provider,
                        registry=self._registry,
                        engine=self._engine,
                        version=self._version,
                        hook_engine=getattr(ctx, "hook_engine", None),
                        context_window=main_win if main_win else 200000,
                    ),
                    usage_sink=usage_sink,
                ),
                timeout=FORK_TIMEOUT,
            )
        except TimeoutError:
            raise TimeoutError(f"fork timeout after {FORK_TIMEOUT:.0f}s")
        # N6：fork 用量计入主会话（状态栏可见 + 压缩感知）
        if ctx.usage_box is not None:
            ctx.usage_box.usage_in += usage_sink["input"]
            ctx.usage_box.usage_out += usage_sink["output"]
        if ctx.runtime is not None:
            ctx.runtime.usage_anchor += usage_sink["input"]
        return final
