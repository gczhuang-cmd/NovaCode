"""SessionRuntime：跨 run 持有的上下文管理长生命周期状态（ch08）。

TUI 的 `run` 主循环持有同一份 SessionRuntime，每轮构造 Agent 时注入；
决策冻结、文件追踪、熔断计数、usage 锚点跨 run 保持。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
)
from ..skills.active import ActiveSkills  # ch11 F24：skills 不 import agent，无循环


@dataclass
class SessionRuntime:
    """跨 run 持有的上下文管理状态。"""

    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    context_window: int = 200000  # 有效上下文窗口（token）
    usage_anchor: int = 0  # 主对话路径 stream 真实 usage 之和；摘要请求不更新
    anchor_msg_len: int = 0  # anchor 当时 conv.length()，下次估算只算这之后的字符增量
    turn_count: int = 0  # ch09 F35：累计自然完成轮数（跨 run），记忆更新触发计数
    active_skills: ActiveSkills | None = None  # ch11 F24：已激活 Skill（/clear 清空）
    # ch12：Hook 注入的 reminder 队列（本轮有效、下轮取走清空）+ Hook 引擎引用。
    pending_reminders: list[str] = field(default_factory=list)
    hook_engine: object = None  # hook.Engine | None（only_once 重置经 /clear、/resume 调用方转发）
    # asyncio 单线程，无需锁

    def reset_for_new_session(self, ses_ctx: SessionContext) -> None:
        """/clear 开新会话：原子重置全部会话级状态并指向新 SessionContext（ch10）。

        compact 三子状态（replacement/recovery/auto_tracking）重建为新实例、
        usage 锚点与 turn_count 清零；`context_window` 保留（与 provider 相关，
        不随会话切换重置）。writer 与 conv 重建由 /clear handler 负责，不进此接口。
        """
        self.replacement = ContentReplacementState()
        self.recovery = RecoveryState()
        self.auto_tracking = CompactCircuitBreaker()
        self.session = ses_ctx
        self.usage_anchor = 0
        self.anchor_msg_len = 0
        self.turn_count = 0
        if self.active_skills is not None:
            self.active_skills.clear()  # ch11 F25：新会话从干净激活态开始
        # ch12 F21/F27：新会话清空未消费的 hook reminder；hook 引擎 only_once 重置
        # 由调用方（/clear、/resume handler）在调用本方法后另行 await engine.reset_for_new_session()
        self.pending_reminders.clear()

    def append_reminders(self, prompts: list[str]) -> None:
        """追加 hook 注入的 prompt 到本轮 reminder 队列（ch12 F33）。"""
        if prompts:
            self.pending_reminders.extend(prompts)

    def take_reminders(self) -> list[str]:
        """取出并清空本轮待注入的 reminder（Agent.run 每轮 reminder 拼接时调用）。"""
        out, self.pending_reminders = self.pending_reminders, []
        return out
