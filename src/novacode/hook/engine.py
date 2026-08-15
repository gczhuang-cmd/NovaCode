"""Hook 引擎：事件分派、only_once 集合、动作执行协调（ch12 F31/N2/N5）。"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from .event import Event, is_blocking
from .executor import Executor
from .matcher import eval_condition
from .rule import Payload, Rule


@dataclass
class DispatchResult:
    """一次事件分派的结果：拦截判定 + 待注入 prompt 集合（F31）。"""

    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)


class Engine:
    """按声明顺序驱动匹配事件的所有 hook；拦截类事件下首个 block 即中断后续。"""

    def __init__(self, rules: list[Rule], sources: list[str]) -> None:
        self._rules = rules  # 按加载顺序
        self._sources = sources  # 加载来源文件列表，供 /hooks 显示
        self._once_fired: set[str] = set()  # only_once 已触发的 hook name
        self._lock = asyncio.Lock()
        self._executor = Executor()

    async def dispatch(self, event: Event, payload: Payload) -> DispatchResult:
        """分派一个生命周期事件；返回拦截判定与待注入 prompt（F31/F33）。

        - async hook：起后台 task 立即继续，不参与 Blocked / InjectedPrompts（F28）；
        - 同步 hook：等结果；拦截类事件下 outcome.blocked 设置 result 并 break（F19/F25）；
        - hook 自身失败：stderr 记一行 `[hook <name>] <event> failed: <reason>`（F29）。
        """
        result = DispatchResult()
        for rule in self._rules:
            if rule.event is not event:
                continue
            async with self._lock:
                if rule.only_once and rule.name in self._once_fired:
                    continue
            if not eval_condition(rule.condition, payload):
                continue

            if rule.asyncio_mode:
                # async hook：起后台 task、立即往下走，不参与拦截 / prompt 收集
                asyncio.create_task(self._executor.run(rule, payload, blocking=False))
                if rule.only_once:
                    async with self._lock:
                        self._once_fired.add(rule.name)
                continue

            outcome = await self._executor.run(rule, payload, blocking=is_blocking(event))
            if outcome.err is not None:
                print(
                    f"[hook {rule.name}] {event.value} failed: {outcome.err}",
                    file=sys.stderr,
                )
                continue
            if outcome.prompt:
                result.injected_prompts.append(outcome.prompt)
            if rule.only_once:
                async with self._lock:
                    self._once_fired.add(rule.name)
            if outcome.blocked and is_blocking(event):
                result.blocked = True
                result.reason = outcome.reason
                result.blocking_hook_name = rule.name
                break
        return result

    async def reset_for_new_session(self) -> None:
        """清空 only_once 集合（/clear、/resume 进新会话时调用，F27/N5）。"""
        async with self._lock:
            self._once_fired.clear()

    @property
    def sources(self) -> list[str]:
        """加载来源文件列表（副本）。"""
        return list(self._sources)

    @property
    def rules(self) -> list[Rule]:
        """已加载规则列表（副本）。"""
        return list(self._rules)
