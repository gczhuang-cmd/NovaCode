"""agent ReAct 循环编排：多轮调 LLM、执行工具、结果回灌，直到任务完成。

对 ch03 单轮闭环的推广：`Agent.run` 现在是 ReAct 循环（`for` 迭代直到自然完成 /
迭代上限 / 用户取消 / 连续未知工具 / 流出错）。对外吐出一条 `Event` async generator
供 TUI 渲染。只依赖 llm/tool/conversation/prompt，不 import anthropic/openai，保持协议无关。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path

from .. import llm, memory, prompt, tool
from ..compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    ManageOutput,
    RecoveryState,
    TriggerKind,
    estimate_tokens,
    manage_context,
    new_session_context,
    usage_anchor,
)
from ..compact.const import AUTO_SAFETY_MARGIN, MANUAL_SAFETY_MARGIN, SUMMARY_RESERVE
from ..conversation import Conversation
from ..hook import DispatchResult, Payload
from ..hook import Event as HookEvent  # 本模块已有 agent.Event（ReAct 事件流），hook 枚举用别名
from ..llm import Message, PromptTooLongError, Provider, ToolCall, ToolDelta, ToolResult
from ..permission import Decision, Engine, Mode, Outcome
from ..skills.adapter import active_to_prompt_entries, catalog_to_prompt_items  # ch11 F22
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)

# 显式记忆请求关键词（忽略大小写，F35）
_MEMORY_SIGNAL_WORDS = ("记住", "记忆", "别忘", "remember", "memo")


def _has_memory_signal(msgs: list[Message]) -> bool:
    """本轮消息是否含显式记忆请求关键词（F35 条件②）。"""
    for m in msgs:
        text = (m.content or "").lower()
        if any(w in text for w in _MEMORY_SIGNAL_WORDS):
            return True
    return False


def _last_user_text(msgs: list[Message]) -> str:
    """conversation 末尾最近的 user 消息文本（Hook PreUserMessage payload，ch12 F10）。"""
    for m in reversed(msgs):
        if m.role == "user":
            return m.content or ""
    return ""


# 迭代上限兜底（F2），内置不可配
MAX_ITERATIONS: int = 25
# 连续「整轮只产生未知工具调用」的迭代数上限（F2）
MAX_UNKNOWN_RUN: int = 3
# 规划模式完整提醒的重复间隔轮次（首轮与每间隔 N 轮注入完整版，其余精简，F7）
PLAN_REMINDER_INTERVAL: int = 4

# 停止/收尾提示文案——既作为 Event(notice=...) 推给 UI，也作为 ensure_assistant_tail 写入历史的兜底文本。
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
# 自然完成时空文本的占位（避免空 assistant 回合破坏下一轮请求）
_FALLBACK_FINAL = "（未生成文本回复。）"


class Phase(IntEnum):
    """工具调用阶段：START=开始执行，END=执行完毕。"""

    START = 0
    END = 1


@dataclass
class ApprovalRequest:
    """人在回路请求：Ask 触发后发往 TUI，TUI 渲染待批准块并 `respond.set_result` 回传三选一。"""

    name: str  # 工具内部名（用于展示 ● name(args)）
    args: str  # 参数预览
    reason: str  # 触发 Ask 的原因（模式 + 类别）
    respond: asyncio.Future[Outcome]  # 单次未来量：TUI 回传用户选择


@dataclass
class Usage:
    """一轮请求的 token 用量（透传 llm.Usage 的语义，含缓存字段）。"""

    input: int = 0
    output: int = 0
    cache_write: int = 0  # 缓存写入（首次创建）
    cache_read: int = 0  # 缓存读取（命中复用）
    reasoning: int = 0  # 本轮思考 token（透传 llm.Usage.reasoning_tokens）


@dataclass
class ToolEvent:
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""

    name: str
    args: str = ""  # 参数预览（用于 ● name(args)）
    phase: Phase = Phase.START
    result: str = ""  # phase=END：结果摘要
    is_error: bool = False  # phase=END：是否错误


class CompactPhase(Enum):
    """压缩生命周期阶段（TUI 据此渲染状态提示，F24a/F24b）。"""

    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass
class CompactEvent:
    """压缩生命周期事件：before/after 状态；err 非 None 表示失败。"""

    phase: CompactPhase
    before: int = 0  # 压缩前估算 token（BEFORE 状态置 0）
    after: int = 0  # 压缩后估算 token
    err: Exception | None = None


@dataclass
class Event:
    """ReAct 循环对外事件流元素，TUI 据非默认字段分派渲染。"""

    text: str = ""  # 模型文本增量（preamble 或最终答复）
    thinking: str = ""  # 思考内容增量（旁路展示，不入对话历史，F6）
    tool_delta: list[ToolDelta] = field(default_factory=list)  # 工具参数增量（旁路展示，F5）
    tool: ToolEvent | None = None  # 工具调用开始/结束
    usage: Usage | None = None  # 本轮 token 用量（每轮 stream 结束后一次）
    iter: int = 0  # >0：进入第 iter 轮迭代（进度提示）
    notice: str = ""  # 系统提示（停止原因等），仅 UI 展示，不入历史
    approval: ApprovalRequest | None = None  # 非空：Ask 待批准（TUI 渲染后 set_result 回传）
    compact: CompactEvent | None = None  # 非空：压缩生命周期状态（TUI 渲染提示，不入历史）
    done: bool = False  # 本轮（整个 Loop）结束
    err: Exception | None = None  # 出错（不中断会话）


@dataclass
class _Summary:
    """请求阶段结束标记（内部）：携带 (text, calls, usage, err)。"""

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    err: Exception | None = None


@dataclass
class _BatchSummary:
    """执行批结束标记（内部）：携带 (results, completed)。"""

    results: list[ToolResult] = field(default_factory=list)
    completed: bool = True


def _args_preview(args: str, limit: int = 80) -> str:
    """工具参数预览：截断到 limit 字符，供工具行 `● name(args)` 显示。"""
    return args if len(args) <= limit else args[: limit - 3] + "..."


def _ensure_final(text: str) -> str:
    """文本非空原样返回；为空返回占位（避免空 assistant 回合破坏下一轮请求）。"""
    return text if text.strip() else _FALLBACK_FINAL


class Agent:
    """持有 provider 与注册中心，执行 Agent Loop（ReAct 循环）。"""

    def __init__(
        self,
        provider: Provider,
        registry: tool.Registry,
        version: str = "",
        engine: Engine | None = None,
        *,
        runtime: SessionRuntime | None = None,
        memory_manager: memory.Manager | None = None,
        instruction_text: str = "",
        memory_text: str = "",
        catalog=None,  # ch11：skills.Catalog，注入第一层 skills-catalog（F21）
        allowed_tools: list[str] | None = None,  # ch11：fork 子 Agent 工具白名单（F28）
        hook_engine=None,  # ch12：hook.Engine | None，构造期注入（F30）
        # ch13：SubAgent 子 Agent 构造选项（spec F10）
        system_prompt: str | None = None,  # 非空时覆盖默认系统提示（run 装配 sys_text 用它）
        max_turns: int = 0,  # 0 表示用全局 MAX_ITERATIONS=25（run 循环上限）
        permission_mode: Mode
        | None = None,  # 子 Agent 启动模式；None 时 run_to_completion 用 DEFAULT
        dont_ask: bool = False,  # 子 Agent Ask 短路为 Allow（黑名单/沙箱/deny 规则仍拦，F12-②）
        approval_upgrader=None,  # ch13：ApprovalUpgrader | None，Ask 升级回调（默认拒绝，F12-③）
        # ch15：Team 队员构造选项
        exclude_tools: list[str] | None = None,  # run 的 defs 过滤（主 Agent 排除协作工具）
        pre_iteration_reminders: callable | None = None,  # 每轮 reminder 来源（队员 mailbox）
        system_suffix: str = "",  # ch15：系统提示词末尾追加（Coordinator 模式纪律段）
    ) -> None:
        if engine is None:
            raise ValueError("Agent 需要注入权限引擎（engine），请用 permission.new_engine 构造")
        self._provider = provider
        self._registry = registry
        self._version = version or "dev"  # 环境信息段的应用版本
        self._engine = engine
        # ch09：记忆管理器与指令/记忆文本。instruction_text 启动加载一次不变（F8）；
        # memory_text 启动加载 + 记忆更新成功落盘后经 refresh_memory_text 刷新（F32）。
        self._mem_mgr = memory_manager
        self._instruction_text = instruction_text
        self._memory_text = memory_text
        self._catalog = catalog  # ch11 F21
        self._allowed_tools = allowed_tools  # ch11 F28（fork 子 Agent 白名单）
        self._hook_engine = hook_engine  # ch12 F30：Hook 引擎（None 表示未启用）
        # ch13：SubAgent 子 Agent 构造选项（spec F10）
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._permission_mode = permission_mode
        self._dont_ask = dont_ask
        self._approval_upgrader = approval_upgrader
        # ch15：Team 队员
        self._exclude_tools = exclude_tools or []
        self._pre_iteration_reminders = pre_iteration_reminders
        self._system_suffix = system_suffix
        # ch08：上下文管理长生命周期状态。TUI run 主循环注入同一份跨轮复用；
        # 未注入给默认空实例（测试场景兼容，所有压缩动作正常跑但不影响既有行为）。
        # ch09 修复：默认 session 目录落到系统临时目录，避免测试/隔离场景污染项目根
        # `.novacode/sessions/`（真实运行由 cli 注入 new_session_context(root)）。
        self.runtime = runtime or SessionRuntime(
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=new_session_context(tempfile.gettempdir()),
            context_window=200000,
        )
        self._run_lock = asyncio.Lock()  # F37：run 与 run_force_compact 互斥
        # ch13：当前 run 的对话引用（Agent 工具 Fork 路径取主对话历史用；无并发 run）
        self._active_conv = None

    def set_permission_mode(self, mode: Mode) -> None:
        """Plan 审批切模式（ch15 F44/F51）：写 _permission_mode（下一轮 run 生效）。"""
        self._permission_mode = mode

    def set_allowed_tools(self, allowed: list[str] | None) -> None:
        """Plan 审批后加回工具（ch15 F51）：写 _allowed_tools。"""
        self._allowed_tools = allowed

    async def run(
        self,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
        """执行 Agent Loop，async generator 吐出事件流。调用方 cancel() 该 task 即终止。

        每次 run 起始采集环境信息、装配稳定系统提示；每轮按 mode + iter 计算 reminder
        （规划模式首轮完整、每间隔 `PLAN_REMINDER_INTERVAL` 轮重复完整、其余精简，F7）。
        ch08：每轮请求前 `manage_context`（第 1 层落盘 + 自动摘要触发判定）；PTL 撞墙紧急
        压缩后同迭代重试一次（F25/F26）；主对话路径 stream 完成后更新 usage 锚点（F13/F14）。
        """
        async with self._run_lock:  # F37：run 与 run_force_compact 互斥
            self._active_conv = conv  # ch13：记录当前对话（Agent 工具 Fork 路径取历史）
            if self._allowed_tools:
                # ch11 F28：fork 子 Agent 按白名单收窄工具集（系统工具豁免）
                defs = self._registry.definitions_filtered(self._allowed_tools)
            else:
                defs = (
                    self._registry.read_only_definitions()
                    if mode == Mode.PLAN
                    else self._registry.definitions()
                )
            # ch15：exclude_tools 过滤（主 Agent 排除 Team 协作工具；不影响 plan 只读收窄）
            if self._exclude_tools:
                excluded = set(self._exclude_tools)
                defs = [d for d in defs if d.name not in excluded]
            env = prompt.gather_environment(self._version, self._provider.model)
            # 稳定系统提示（ch09：指令+记忆；ch11：第一层 skills-catalog 列表，F21）
            catalog_text = ""
            if self._catalog is not None:
                items = [
                    prompt.SkillCatalogItem(i.name, i.description)
                    for i in catalog_to_prompt_items(self._catalog)
                ]
                catalog_text = prompt.render_skills_catalog(items)
            # ch13：子 Agent 的 system_prompt 覆盖默认主 Agent 系统提示（spec F10）
            if self._system_prompt:
                sys_text = self._system_prompt
            else:
                sys_text = prompt.build_system_prompt(
                    self._instruction_text, self._memory_text, catalog_text
                )
            # ch15：系统提示词末尾追加（Coordinator 模式纪律段）
            if self._system_suffix:
                sys_text += self._system_suffix
            # 环境信息段（不缓存）+ 第二层 active-skills 块（F22/N3）
            env_text = env.render()
            if self.runtime is not None and self.runtime.active_skills is not None:
                entries = [
                    prompt.ActiveSkillEntry(e.name, e.body)
                    for e in active_to_prompt_entries(self.runtime.active_skills)
                ]
                skills_block = prompt.render_active_skills_block(entries)
                if skills_block:
                    env_text += "\n\n" + skills_block
            unknown_run = 0
            # ch13：子 Agent 用 frontmatter 的 max_turns 限制轮数；主 Agent 传 0 仍用全局 25
            max_iter = self._max_turns or MAX_ITERATIONS
            for it in range(1, max_iter + 1):
                yield Event(iter=it)  # 进度提示（F9）
                if cancel.is_set():
                    self._finish_cancelled(conv)
                    return
                reminder = ""
                if mode == Mode.PLAN:
                    full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
                    reminder = prompt.plan_reminder(full)
                # ch12 F33：拼接 hook 注入的 prompt（置于 plan reminder 之后；非 plan 模式同样生效）
                pending = self.runtime.take_reminders()
                # ch15：队员 mailbox 每轮注入（pre_iteration_reminders 闭包）
                if self._pre_iteration_reminders is not None:
                    try:
                        extra = await self._pre_iteration_reminders()
                    except Exception:  # noqa: BLE001 邮箱读取失败不阻断 Loop
                        extra = []
                    if extra:
                        pending = list(pending) + list(extra)
                if pending:
                    hook_block = prompt.system_reminder("\n".join(pending))
                    reminder = (reminder + "\n\n" if reminder else "") + hook_block
                # ch08：请求前管理上下文（第 1 层落盘 + 自动摘要触发判定，F6/F7）
                est = estimate_tokens(
                    self.runtime.usage_anchor, conv.messages(), self.runtime.anchor_msg_len
                )
                will_summarize = (
                    est >= self.runtime.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
                )
                if will_summarize:
                    # ch12：PreCompact（自动路径，manage_context 调用之前）
                    await self._dispatch_hook(
                        HookEvent.PRE_COMPACT,
                        self._hook_base_payload(mode, HookEvent.PRE_COMPACT) | {"trigger": "auto"},
                    )
                    yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO))
                try:
                    mc_out = await self._manage_context(conv, defs, est, TriggerKind.AUTO)
                    mc_err: Exception | None = None
                except Exception as e:  # noqa: BLE001 压缩失败不阻断：走错误流程
                    mc_err = e
                    mc_out = ManageOutput(before_tokens=est, after_tokens=0)
                if will_summarize:
                    # ch12：PostCompact（manage_context 返回后）
                    await self._dispatch_hook(
                        HookEvent.POST_COMPACT,
                        self._hook_base_payload(mode, HookEvent.POST_COMPACT)
                        | {
                            "trigger": "auto",
                            "before_tokens": mc_out.before_tokens,
                            "after_tokens": mc_out.after_tokens,
                        },
                    )
                    yield Event(
                        compact=CompactEvent(
                            phase=CompactPhase.AFTER_AUTO,
                            before=mc_out.before_tokens,
                            after=mc_out.after_tokens,
                            err=mc_err,
                        )
                    )
                if mc_err is not None:
                    yield Event(err=mc_err)
                    self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                    return
                # 一轮请求：转发文本增量（err 已在 _request 内 yield）
                summary: _Summary | None = None
                async for item in self._request(
                    conv, defs, sys_text, env_text, reminder, cancel, mode
                ):
                    if isinstance(item, _Summary):
                        summary = item
                        break
                    yield item
                assert summary is not None
                if summary.err is not None:
                    # ch08 F25/F26：PTL 撞墙 → 紧急压缩 → 重试一次（迭代级只重试一次）
                    if isinstance(summary.err, PromptTooLongError):
                        emergency_retried = False
                        if not emergency_retried:
                            # ch12：PreCompact（紧急路径）
                            await self._dispatch_hook(
                                HookEvent.PRE_COMPACT,
                                self._hook_base_payload(mode, HookEvent.PRE_COMPACT)
                                | {"trigger": "emergency"},
                            )
                            yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY))
                            try:
                                em_out = await self._manage_context(
                                    conv, defs, est, TriggerKind.EMERGENCY
                                )
                                em_err: Exception | None = None
                            except Exception as e:  # noqa: BLE001 紧急压缩失败
                                em_err = e
                                em_out = ManageOutput(before_tokens=est, after_tokens=0)
                            # ch12：PostCompact（紧急路径）
                            await self._dispatch_hook(
                                HookEvent.POST_COMPACT,
                                self._hook_base_payload(mode, HookEvent.POST_COMPACT)
                                | {
                                    "trigger": "emergency",
                                    "before_tokens": em_out.before_tokens,
                                    "after_tokens": em_out.after_tokens,
                                },
                            )
                            yield Event(
                                compact=CompactEvent(
                                    phase=CompactPhase.AFTER_EMERGENCY,
                                    before=em_out.before_tokens,
                                    after=em_out.after_tokens,
                                    err=em_err,
                                )
                            )
                            if em_err is not None:
                                yield Event(err=em_err)
                                self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                                return
                            # 重置锚点 + 重估；低于安全线才重试（F25a）
                            self.runtime.usage_anchor = 0
                            self.runtime.anchor_msg_len = 0
                            est2 = estimate_tokens(0, conv.messages(), 0)
                            if est2 >= self.runtime.context_window - MANUAL_SAFETY_MARGIN:
                                yield Event(err=summary.err)
                                self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                                return
                            emergency_retried = True
                            summary = None
                            async for item in self._request(
                                conv, defs, sys_text, env_text, reminder, cancel, mode
                            ):
                                if isinstance(item, _Summary):
                                    summary = item
                                    break
                                yield item
                            if summary is None:
                                self._finish_cancelled(conv)
                                return
                    if summary.err is not None:
                        yield Event(err=summary.err)
                        self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                        return
                if cancel.is_set():
                    # 用户取消：最高优先级收尾
                    self._finish_cancelled(conv)
                    return
                if summary.usage is not None:
                    # ch08 F13/F14：更新 usage 锚点（替换非累加）+ 消息长度锚点
                    self.runtime.usage_anchor = usage_anchor(summary.usage)
                    self.runtime.anchor_msg_len = conv.length()
                    yield Event(
                        usage=Usage(
                            summary.usage.input_tokens,
                            summary.usage.output_tokens,
                            summary.usage.cache_write,
                            summary.usage.cache_read,
                            summary.usage.reasoning_tokens,
                        )
                    )
                if not summary.calls:
                    # 自然完成：纯文本即最终答复（F2-1）
                    conv.add_assistant(_ensure_final(summary.text))
                    self._maybe_trigger_memory_update(conv)  # ch09 F35/F36
                    # ch12：Stop hook（Done 事件之前；取消/出错路径不触发）
                    await self._dispatch_hook(
                        HookEvent.STOP, self._hook_base_payload(mode, HookEvent.STOP) | {"iter": it}
                    )
                    yield Event(done=True)
                    return
                conv.add_assistant_with_tool_calls(summary.text, summary.calls)
                unknown_run = unknown_run + 1 if self._all_unknown(summary.calls) else 0
                # 保序分批执行（F5）：工具事件在 _execute_batched 内转发（接入权限判定 ch06）
                batch: _BatchSummary | None = None
                async for item in self._execute_batched(summary.calls, mode, cancel):
                    if isinstance(item, _BatchSummary):
                        batch = item
                        break
                    yield item
                assert batch is not None
                conv.add_tool_results(batch.results)  # 无条件回灌（含已取消占位，F6）
                if not batch.completed:
                    # 执行中取消：最高优先级终止
                    self._ensure_assistant_tail(conv, NOTICE_CANCELLED)
                    return
                if unknown_run >= MAX_UNKNOWN_RUN:
                    # 连续未知工具（F2-4）
                    yield Event(notice=NOTICE_UNKNOWN_TOOLS)
                    self._ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                    # ch12：Stop hook（Done 之前）
                    await self._dispatch_hook(
                        HookEvent.STOP, self._hook_base_payload(mode, HookEvent.STOP) | {"iter": it}
                    )
                    yield Event(done=True)
                    return
            # 触达迭代上限（F2-2）
            yield Event(notice=NOTICE_MAX_ITER)
            self._ensure_assistant_tail(conv, NOTICE_MAX_ITER)
            # ch12：Stop hook（Done 之前）
            await self._dispatch_hook(
                HookEvent.STOP, self._hook_base_payload(mode, HookEvent.STOP) | {"iter": it}
            )
            yield Event(done=True)

    async def _manage_context(
        self,
        conv: Conversation,
        defs: list,
        estimated_token: int,
        trigger: TriggerKind,
    ) -> ManageOutput:
        """构造 ManageInput 并调 manage_context（ch08）。"""
        in_ = ManageInput(
            conv=conv,
            provider=self._provider,
            model=self._provider.model,
            context_window=self.runtime.context_window,
            tool_defs=defs,
            replacement=self.runtime.replacement,
            recovery=self.runtime.recovery,
            auto_tracking=self.runtime.auto_tracking,
            session=self.runtime.session,
            usage_anchor=self.runtime.usage_anchor,
            anchor_msg_len=self.runtime.anchor_msg_len,
            estimated_token=estimated_token,
            trigger=trigger,
        )
        return await manage_context(in_)

    async def run_force_compact(self, conv: Conversation, tool_defs: list) -> tuple[int, int]:
        """手动 /compact 入口：与 run 互斥，无条件触发摘要（ch08 F22/F37）。"""
        async with self._run_lock:
            est = estimate_tokens(
                self.runtime.usage_anchor, conv.messages(), self.runtime.anchor_msg_len
            )
            in_ = ManageInput(
                conv=conv,
                provider=self._provider,
                model=self._provider.model,
                context_window=self.runtime.context_window,
                tool_defs=tool_defs,
                replacement=self.runtime.replacement,
                recovery=self.runtime.recovery,
                auto_tracking=self.runtime.auto_tracking,
                session=self.runtime.session,
                usage_anchor=self.runtime.usage_anchor,
                anchor_msg_len=self.runtime.anchor_msg_len,
                estimated_token=est,
                trigger=TriggerKind.MANUAL,
            )
            # ch12：PreCompact / PostCompact（手动路径）
            await self._dispatch_hook(
                HookEvent.PRE_COMPACT,
                self._hook_base_payload(Mode.DEFAULT, HookEvent.PRE_COMPACT)
                | {"trigger": "manual"},
            )
            out = await manage_context(in_)
            await self._dispatch_hook(
                HookEvent.POST_COMPACT,
                self._hook_base_payload(Mode.DEFAULT, HookEvent.POST_COMPACT)
                | {
                    "trigger": "manual",
                    "before_tokens": out.before_tokens,
                    "after_tokens": out.after_tokens,
                },
            )
            return out.before_tokens, out.after_tokens

    async def _request(
        self,
        conv: Conversation,
        defs: list,
        sys_text: str,
        env_text: str,
        reminder: str,
        cancel: asyncio.Event,
        mode: Mode,
    ) -> AsyncIterator[Event | _Summary]:
        """发起一次请求：组装 `Request` 调 provider.stream；转发文本增量，结束 yield `_Summary`。

        流式期间监听 cancel：取消则停止收集并正常返回汇总（err=None）。
        """
        parts: list[str] = []
        calls: list[ToolCall] = []
        usage = None
        err: Exception | None = None
        req = llm.Request(
            messages=conv.messages(),
            tools=defs,
            system=llm.System(stable=sys_text, environment=env_text),
            reminder=reminder,
        )
        # ch12：PreUserMessage hook（provider.stream 之前；payload 含末尾 user 消息）
        await self._dispatch_hook(
            HookEvent.PRE_USER_MESSAGE,
            self._hook_base_payload(mode, HookEvent.PRE_USER_MESSAGE)
            | {"prompt": _last_user_text(conv.messages())},
        )
        async for se in self._provider.stream(req):
            if cancel.is_set():
                break
            if se.err is not None:
                # ch12：Notification（stream_error）在 emit Err 之前
                await self._dispatch_hook(
                    HookEvent.NOTIFICATION,
                    self._hook_base_payload(mode, HookEvent.NOTIFICATION)
                    | {"kind": "stream_error", "detail": str(se.err)},
                )
                err = se.err
                yield Event(err=err)
                break
            if se.usage is not None:
                usage = se.usage
            if se.thinking:
                # 思考增量：旁路展示，不进 parts（不进入对话历史/JSONL，F6）
                yield Event(thinking=se.thinking)
            if se.tool_delta:
                # 工具参数增量：旁路展示，不改变 calls（完整调用仍由 tool_calls 拼，F5）
                yield Event(tool_delta=se.tool_delta)
            if se.text:
                parts.append(se.text)
                yield Event(text=se.text)
            if se.tool_calls:
                calls.extend(se.tool_calls)
            if se.done:
                break
        yield _Summary(text="".join(parts), calls=calls, usage=usage, err=err)

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event | _BatchSummary]:
        """保序分批执行：连续只读合并并发、有副作用串行（F5）；每个工具前先过权限判定。

        权限（ch06）：每个调用先 `engine.check(mode, call, read_only)`——
        Allow 执行、Deny 直接构造被拒结果回灌、Ask（仅串行批会走到）发 `Event(approval=...)`
        等人在回路。只读批永不 Ask，保序并发不退化（N3）。
        事件顺序：PHASE_START 按序、PHASE_END 按序（并发只发生在执行环节）。
        取消：已发起未完成的调用补「已取消」结果，completed=False。
        """
        n = len(calls)
        results: list[ToolResult | None] = [None] * n
        i = 0
        while i < n:
            if cancel.is_set():
                for k in range(i, n):
                    results[k] = ToolResult(
                        tool_call_id=calls[k].id, content=NOTICE_CANCELLED, is_error=True
                    )
                yield _BatchSummary(results=[r for r in results if r is not None], completed=False)
                return
            if self._registry.is_read_only(calls[i].name):
                # 吃最长连续只读区间 [i, j)：逐个权限预检（只读永不 Ask），Deny 直接预置被拒结果
                j = i
                while j < n and self._registry.is_read_only(calls[j].name):
                    j += 1
                allowed: list[int] = []
                for k in range(i, j):
                    # ch12：PreToolUse hook（权限 check 之前；拦截则跳过权限与执行，F32）
                    pre = await self._dispatch_hook(
                        HookEvent.PRE_TOOL_USE,
                        self._hook_base_payload(mode, HookEvent.PRE_TOOL_USE)
                        | {
                            "tool_name": calls[k].name,
                            "tool_input": self._parse_call_input(calls[k]),
                        },
                    )
                    if pre.blocked:
                        results[k] = self._hook_blocked_result(
                            calls[k].id, pre.blocking_hook_name, pre.reason
                        )
                        continue
                    decision, reason = self._engine.check(mode, calls[k], True)
                    if decision == Decision.DENY:
                        results[k] = ToolResult(
                            tool_call_id=calls[k].id, content=reason, is_error=True
                        )
                    else:
                        allowed.append(k)
                for k in range(i, j):  # 按调用序发 START（含被拒/拦截项）
                    yield Event(
                        tool=ToolEvent(
                            name=calls[k].name,
                            args=_args_preview(calls[k].input),
                            phase=Phase.START,
                        )
                    )
                tasks = {k: asyncio.create_task(self._run_one(calls[k])) for k in allowed}
                if tasks:
                    cancel_watcher = asyncio.create_task(cancel.wait())
                    done, pending = await asyncio.wait(
                        [*tasks.values(), cancel_watcher], return_when=asyncio.FIRST_COMPLETED
                    )
                    if cancel_watcher in done:
                        # 取消：放弃未完成工具，补已取消结果
                        for t in pending:
                            t.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        for k, t in tasks.items():
                            if t in done:
                                results[k] = self._safe_task_result(t, calls[k])
                            else:
                                results[k] = ToolResult(
                                    tool_call_id=calls[k].id,
                                    content=NOTICE_CANCELLED,
                                    is_error=True,
                                )
                        for k in range(j, n):
                            results[k] = ToolResult(
                                tool_call_id=calls[k].id, content=NOTICE_CANCELLED, is_error=True
                            )
                        yield _BatchSummary(
                            results=[r for r in results if r is not None], completed=False
                        )
                        return
                    cancel_watcher.cancel()
                    # FIRST_COMPLETED 可能只完成部分工具：等本批全部完成后统一取结果（避免 Result is not set）
                    await asyncio.wait(list(tasks.values()))
                    for k, t in tasks.items():
                        results[k] = self._safe_task_result(t, calls[k])
                        await self._maybe_record_file(calls[k], results[k])  # ch08 F19
                for k in range(
                    i, j
                ):  # ch12：PostToolUse（emit PhaseEnd 之前；拦截/Deny 项 is_error=True）
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        self._hook_base_payload(mode, HookEvent.POST_TOOL_USE)
                        | {
                            "tool_name": calls[k].name,
                            "tool_input": self._parse_call_input(calls[k]),
                            "tool_result": results[k].content,
                            "is_error": results[k].is_error,
                        },
                    )
                for k in range(i, j):  # 按调用序发 END（含被拒/拦截项，is_error=True）
                    yield Event(
                        tool=ToolEvent(
                            name=calls[k].name,
                            args=_args_preview(calls[k].input),
                            phase=Phase.END,
                            result=results[k].content,
                            is_error=results[k].is_error,
                        )
                    )
                i = j
            else:
                # 串行执行单个有副作用工具（Hook 拦截 → 权限判定 → 执行 / 被拒 / 人在回路）
                call = calls[i]
                preview = _args_preview(call.input)
                # ch12：PreToolUse hook（权限 check 之前；拦截则跳过权限与执行，F32）
                pre = await self._dispatch_hook(
                    HookEvent.PRE_TOOL_USE,
                    self._hook_base_payload(mode, HookEvent.PRE_TOOL_USE)
                    | {"tool_name": call.name, "tool_input": self._parse_call_input(call)},
                )
                if pre.blocked:
                    results[i] = self._hook_blocked_result(
                        call.id, pre.blocking_hook_name, pre.reason
                    )
                    yield Event(tool=ToolEvent(name=call.name, args=preview, phase=Phase.START))
                    yield Event(
                        tool=ToolEvent(
                            name=call.name,
                            args=preview,
                            phase=Phase.END,
                            result=results[i].content,
                            is_error=results[i].is_error,
                        )
                    )
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        self._hook_base_payload(mode, HookEvent.POST_TOOL_USE)
                        | {
                            "tool_name": call.name,
                            "tool_input": self._parse_call_input(call),
                            "tool_result": results[i].content,
                            "is_error": results[i].is_error,
                        },
                    )
                    i += 1
                    continue
                decision, reason = self._engine.check(mode, call, False)
                # ch13：子 Agent dontAsk 兜底（黑名单/沙箱/deny 规则已在前四层拦截，此处仅放行 Ask）
                if decision == Decision.ASK and self._dont_ask:
                    decision = Decision.ALLOW
                yield Event(tool=ToolEvent(name=call.name, args=preview, phase=Phase.START))
                if decision == Decision.DENY:
                    results[i] = ToolResult(tool_call_id=call.id, content=reason, is_error=True)
                    yield Event(
                        tool=ToolEvent(
                            name=call.name,
                            args=preview,
                            phase=Phase.END,
                            result=reason,
                            is_error=True,
                        )
                    )
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        self._hook_base_payload(mode, HookEvent.POST_TOOL_USE)
                        | {
                            "tool_name": call.name,
                            "tool_input": self._parse_call_input(call),
                            "tool_result": reason,
                            "is_error": True,
                        },
                    )
                    i += 1
                    continue
                if decision == Decision.ASK:
                    # ch13：子 Agent 审批升级回调（默认拒绝本次；ok=False 走下方默认 emit 路径）
                    upgrade_decided = False
                    if self._approval_upgrader is not None:
                        outcome, ok = await self._approval_upgrader(
                            ApprovalRequest(
                                name=call.name,
                                args=preview,
                                reason=reason,
                                respond=asyncio.get_running_loop().create_future(),
                            )
                        )
                        if ok:
                            upgrade_decided = True
                            if outcome == Outcome.ALLOW_FOREVER:
                                try:
                                    self._engine.persist_local_allow(call)
                                except Exception as exc:  # noqa: BLE001 永久放行失败仅记日志
                                    logger.warning("永久放行规则写入失败: %s", exc)
                                decision = Decision.ALLOW
                            elif outcome == Outcome.ALLOW_ONCE:
                                decision = Decision.ALLOW
                            else:  # DENY_ONCE：拒绝本次，结果回灌（对齐既有 DENY 分支渲染）
                                text = "用户拒绝本次操作（子 Agent 审批默认拒绝）"
                                results[i] = ToolResult(
                                    tool_call_id=call.id, content=text, is_error=True
                                )
                                yield Event(
                                    tool=ToolEvent(name=call.name, args=preview, phase=Phase.START)
                                )
                                yield Event(
                                    tool=ToolEvent(
                                        name=call.name,
                                        args=preview,
                                        phase=Phase.END,
                                        result=text,
                                        is_error=True,
                                    )
                                )
                                await self._dispatch_hook(
                                    HookEvent.POST_TOOL_USE,
                                    self._hook_base_payload(mode, HookEvent.POST_TOOL_USE)
                                    | {
                                        "tool_name": call.name,
                                        "tool_input": self._parse_call_input(call),
                                        "tool_result": text,
                                        "is_error": True,
                                    },
                                )
                                i += 1
                                continue
                    if not upgrade_decided:
                        # ch12：Notification（approval）在发 ApprovalRequest 之前
                        await self._dispatch_hook(
                            HookEvent.NOTIFICATION,
                            self._hook_base_payload(mode, HookEvent.NOTIFICATION)
                            | {"kind": "approval", "detail": call.name},
                        )
                        # 第五层：发待批准事件，等 TUI 回传三选一（与 cancel Event 竞态）
                        respond: asyncio.Future[Outcome] = (
                            asyncio.get_running_loop().create_future()
                        )
                        yield Event(
                            approval=ApprovalRequest(
                                name=call.name, args=preview, reason=reason, respond=respond
                            )
                        )
                        watcher = asyncio.create_task(cancel.wait())
                        done, _ = await asyncio.wait(
                            [respond, watcher], return_when=asyncio.FIRST_COMPLETED
                        )
                        watcher.cancel()
                        if watcher in done:
                            # 用户在待批准态取消：丢弃悬挂 respond，走既有取消收尾
                            respond.cancel()
                            results[i] = ToolResult(
                                tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True
                            )
                            for k in range(i + 1, n):
                                results[k] = ToolResult(
                                    tool_call_id=calls[k].id,
                                    content=NOTICE_CANCELLED,
                                    is_error=True,
                                )
                            yield _BatchSummary(
                                results=[r for r in results if r is not None], completed=False
                            )
                            return
                        outcome = respond.result()
                        if outcome == Outcome.ALLOW_FOREVER:
                            try:
                                self._engine.persist_local_allow(call)
                            except Exception as exc:  # noqa: BLE001 永久放行失败仅记日志，不阻断执行
                                logger.warning("永久放行规则写入失败: %s", exc)
                        if outcome == Outcome.DENY_ONCE:
                            text = "用户拒绝本次操作"
                            results[i] = ToolResult(
                                tool_call_id=call.id, content=text, is_error=True
                            )
                            yield Event(
                                tool=ToolEvent(
                                    name=call.name,
                                    args=preview,
                                    phase=Phase.END,
                                    result=text,
                                    is_error=True,
                                )
                            )
                            await self._dispatch_hook(
                                HookEvent.POST_TOOL_USE,
                                self._hook_base_payload(mode, HookEvent.POST_TOOL_USE)
                                | {
                                    "tool_name": call.name,
                                    "tool_input": self._parse_call_input(call),
                                    "tool_result": text,
                                    "is_error": True,
                                },
                            )
                            i += 1
                            continue
                        # ALLOW_ONCE / ALLOW_FOREVER：落到下面执行
                # 执行单个有副作用工具（同样监听取消）
                exec_task = asyncio.create_task(self._run_one(call))
                cancel_watcher = asyncio.create_task(cancel.wait())
                done, pending = await asyncio.wait(
                    [exec_task, cancel_watcher], return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_watcher in done:
                    exec_task.cancel()
                    await asyncio.gather(exec_task, return_exceptions=True)
                    results[i] = ToolResult(
                        tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True
                    )
                    for k in range(i + 1, n):
                        results[k] = ToolResult(
                            tool_call_id=calls[k].id, content=NOTICE_CANCELLED, is_error=True
                        )
                    yield _BatchSummary(
                        results=[r for r in results if r is not None], completed=False
                    )
                    return
                cancel_watcher.cancel()
                r = exec_task.result()
                await self._maybe_record_file(call, r)  # ch08 F19：ReadFile 追踪
                results[i] = ToolResult(
                    tool_call_id=call.id, content=r.content, is_error=r.is_error
                )
                # ch12：PostToolUse（拿到 result 后、emit PhaseEnd 之前）
                await self._dispatch_hook(
                    HookEvent.POST_TOOL_USE,
                    self._hook_base_payload(mode, HookEvent.POST_TOOL_USE)
                    | {
                        "tool_name": call.name,
                        "tool_input": self._parse_call_input(call),
                        "tool_result": r.content,
                        "is_error": r.is_error,
                    },
                )
                yield Event(
                    tool=ToolEvent(
                        name=call.name,
                        args=preview,
                        phase=Phase.END,
                        result=r.content,
                        is_error=r.is_error,
                    )
                )
                i += 1
        yield _BatchSummary(results=[r for r in results if r is not None], completed=True)

    async def _maybe_record_file(self, call: ToolCall, result: ToolResult) -> None:
        """ReadFile 成功后用纯净字节记录到 recovery（ch08 F19/F19a）。

        同 task 顺序、`add_tool_results` 之前调用；读盘失败静默吞掉（N6）。
        """
        if call.name != "read_file" or result.is_error:
            return
        try:
            data = json.loads(call.input or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        path = data.get("path")
        if not isinstance(path, str) or not path:
            return
        try:
            abs_path = str(Path(path).resolve())
            content = await asyncio.to_thread(Path(abs_path).read_bytes)
        except OSError:
            return
        self.runtime.recovery.record_file(abs_path, content.decode("utf-8", errors="replace"))

    async def _run_one(self, call: ToolCall) -> ToolResult:
        """执行单个工具调用，返回结构化结果（超时转错误，保证 tool_calls 闭合）。

        注：`wait_for` 超时若直接抛 TimeoutError 会冒泡中断批执行，assistant 的
        tool_calls 得不到 tool_result 回灌 → conv 留下孤儿 tool_calls → 后续请求
        400（assistant tool_calls must be followed by tool messages）。必须 catch
        转结构化错误结果，让 `add_tool_results` 用完整结果闭合。
        """
        try:
            r = await asyncio.wait_for(
                self._registry.execute(call.name, call.input), tool.DEFAULT_TIMEOUT
            )
        except TimeoutError:
            return ToolResult(
                tool_call_id=call.id,
                content=f"工具 {call.name} 执行超时（> {tool.DEFAULT_TIMEOUT:.0f}s），已取消",
                is_error=True,
            )
        return ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error)

    @staticmethod
    def _safe_task_result(t, call: ToolCall) -> ToolResult:
        """取并发工具 task 结果；异常/超时转结构化错误（保证 tool_calls 闭合）。"""
        try:
            return t.result()
        except TimeoutError:
            return ToolResult(
                tool_call_id=call.id, content=f"工具 {call.name} 执行超时", is_error=True
            )
        except BaseException as e:  # noqa: BLE001 工具异常不崩批处理，转错误结果闭合
            return ToolResult(
                tool_call_id=call.id, content=f"工具 {call.name} 执行失败: {e}", is_error=True
            )

    def _all_unknown(self, calls: list[ToolCall]) -> bool:
        """全部调用都是未注册工具才返回 True（混入任一已注册即视为有进展，计数重置）。"""
        return all(self._registry.get(c.name) is None for c in calls)

    def _ensure_assistant_tail(self, conv: Conversation, fallback: str) -> None:
        """若历史不以 assistant 文本收尾，追加 fallback，保证角色交替（F6）。"""
        if conv.last_role() != "assistant":
            conv.add_assistant(fallback)

    def _finish_cancelled(self, conv: Conversation) -> None:
        """取消路径统一收尾：保证历史以 assistant 收尾，不 yield notice。"""
        self._ensure_assistant_tail(conv, NOTICE_CANCELLED)

    def _maybe_trigger_memory_update(self, conv: Conversation) -> None:
        """自然完成后：每 5 轮或检测到显式记忆请求时异步发起记忆更新（ch09 F35/F36）。

        更新在独立 asyncio task 中运行，不阻塞用户的下一次输入。
        """
        if self._mem_mgr is None:
            return
        self.runtime.turn_count += 1
        recent = self._extract_recent_turn(conv)
        if self.runtime.turn_count % 5 == 0 or _has_memory_signal(recent):
            self._mem_mgr.launch(recent)  # launch 内部 create_task + 追踪（F36/R12）

    @staticmethod
    def _extract_recent_turn(conv: Conversation) -> list[Message]:
        """提取最近一轮消息：从最后一条 user 到当前 assistant 收尾（F37）。"""
        msgs = conv.messages()
        start = len(msgs)
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].role == "user":
                start = i
                break
        return list(msgs[start:])

    def refresh_memory_text(self, text: str) -> None:
        """记忆更新成功落盘后刷新 memory_text 快照（ch09 F32）。

        由 memory.Manager 的更新回调调用；下一轮请求装配系统提示注入最新索引。
        """
        self._memory_text = text

    def activate_skill(self, name: str, body: str) -> None:
        """把 Skill 钉到 Active 列表（ch11 F23/F24）。"""
        if self.runtime is not None and self.runtime.active_skills is not None:
            self.runtime.active_skills.activate(name, body)

    def clear_active_skills(self) -> None:
        """清空已激活 Skill（/clear 用，ch11 F25）。"""
        if self.runtime is not None and self.runtime.active_skills is not None:
            self.runtime.active_skills.clear()

    # ── ch12 Hook 接入 ────────────────────────────────────────────────────────

    async def _dispatch_hook(self, event: HookEvent, payload: Payload) -> DispatchResult:
        """调 Hook 引擎分派事件；把注入的 prompt 写入 runtime 队列（F31/F33）。

        未启用 Hook（hook_engine=None）返回空结果，不影响主流程。
        """
        if self._hook_engine is None:
            return DispatchResult()
        result = await self._hook_engine.dispatch(event, payload)
        if result.injected_prompts and self.runtime is not None:
            self.runtime.append_reminders(result.injected_prompts)
        return result

    def _hook_base_payload(self, mode: Mode, event: HookEvent) -> Payload:
        """Hook payload 通用字段（F10）：session_id 取当前会话、mode 用展示名。"""
        session_id = ""
        if self.runtime is not None and self.runtime.session is not None:
            session_id = self.runtime.session.session_id
        return {
            "event": event.value,
            "session_id": session_id,
            "cwd": os.getcwd(),
            "mode": str(mode),
        }

    @staticmethod
    def _parse_call_input(call: ToolCall) -> dict:
        """解析工具参数 JSON 为 dict（Hook payload 的 tool_input 字段，F10）。"""
        try:
            data = json.loads(call.input or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _hook_blocked_result(call_id: str, hook_name: str, reason: str) -> ToolResult:
        """PreToolUse 拦截结果：`[hook <name>] <reason>` 当 tool_result 回灌（F32）。"""
        return ToolResult(
            tool_call_id=call_id,
            content=f"[hook {hook_name}] {reason}",
            is_error=True,
        )


# ch13：run_to_completion 挂载到 Agent（spec F9）。延迟导入放文件末尾，确保
# NOTICE_MAX_ITER / MAX_ITERATIONS 等模块级常量已定义，避免循环导入。
from .run_to_completion import MaxTurnsReached as MaxTurnsReached
from .run_to_completion import run_to_completion

Agent.run_to_completion = run_to_completion
