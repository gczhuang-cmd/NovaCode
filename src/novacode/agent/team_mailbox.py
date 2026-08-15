"""队员 Loop 邮箱注入（ch15 T20/T32，spec F41/F42/F44）。

`ingest_team_mailbox` 返回 `pre_iteration_reminders` 闭包：每轮读 mailbox 未读 →
构造 `<incoming-messages>` reminder → 标 read。若收到 `plan_approval_response`，
按 T32 切换权限模式（需持 agent 引用）。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable


def build_incoming_messages_reminder(msgs) -> str:
    """构造 `<incoming-messages>` reminder（spec F42 格式）。"""
    lines = [f"收到 {len(msgs)} 条新消息:"]
    for i, m in enumerate(msgs, 1):
        ts = getattr(m, "timestamp", 0)
        ts_s = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
        summary = (getattr(m, "summary", "") or "").strip()
        content = (getattr(m, "content", "") or "")[:200]
        lines.append(
            f"[{i}] 来自 {getattr(m, 'from_', '')}"
            f"(type={getattr(m, 'type', '')},ts={ts_s}): {summary}"
        )
        if content:
            lines.append(f"    {content}")
    return "<incoming-messages>\n" + "\n".join(lines) + "\n</incoming-messages>"


def ingest_team_mailbox(
    tc, agent=None, full_allowed: list[str] | None = None
) -> Callable[[], Awaitable[list[str]]]:
    """返回 pre_iteration_reminders 闭包。

    `agent` 非 None 时处理 `plan_approval_response`：approve=True → 切 default + 加回
    写工具（T32）；approve=False → 附加反馈文案（权限模式保持 plan）。
    `full_allowed`：approve 后要恢复的完整工具清单（只读基础 + Team 协作工具）。
    """

    async def _ingest() -> list[str]:
        if tc.read_unread is None:
            return []
        try:
            indices, msgs = await tc.read_unread()
        except Exception:  # noqa: BLE001 邮箱读取失败不阻断 Loop
            return []
        if not msgs:
            return []
        reminders: list[str] = []
        plan_approved = False
        for m in msgs:
            if getattr(m, "type", "") == "plan_approval_response":
                payload = getattr(m, "payload", None) or {}
                if payload.get("approve"):
                    plan_approved = True
                    if agent is not None:
                        from . import Mode

                        agent.set_permission_mode(Mode.DEFAULT)
                        if full_allowed is not None:
                            agent.set_allowed_tools(full_allowed)
                else:
                    feedback = payload.get("feedback", "")
                    reminders.append(
                        "Lead 驳回了计划"
                        + (f"，反馈: {feedback}" if feedback else "")
                        + "。请调整后重新提交计划。"
                    )
        if plan_approved:
            reminders.append("Lead 已批准计划，权限模式已切到 default，可执行计划。")
        reminders.insert(0, build_incoming_messages_reminder(msgs))
        if tc.mark_read is not None:
            try:
                await tc.mark_read(indices)
            except Exception:  # noqa: BLE001,S110
                pass
        return reminders

    return _ingest
