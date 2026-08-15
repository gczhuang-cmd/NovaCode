"""Lead 邮箱轮询与自动唤醒（ch15 T26/T30b，spec F41a/F41b）。

`consume_lead_mail` 每秒轮询所有 Team 的 Lead mailbox，把未读消息转 `<team-update>`
reminder 推 `runtime.pending_reminders` 并 `set()` 唤醒信号；输入框收到信号后退出
（哨兵文本），主循环走自动回合（`_autonomous_turn`）。
"""

from __future__ import annotations

import asyncio

TEAM_UPDATE_LIMIT = 8000  # 单条 content 透传上限（F41a）


def build_team_update_reminder(msgs) -> str:
    """把一批 LeadMessage 渲染成 `<team-update>` reminder（F41a）。"""
    lines = ["队员发来新消息:"]
    for m in msgs:
        content = (m.content or "")[:TEAM_UPDATE_LIMIT]
        lines.append(f"- 来自 {m.from_}(team={m.team_name},type={m.type},ts={m.ts}): {m.summary}")
        if content:
            lines.append(f"  {content}")
    return "<team-update>\n" + "\n".join(lines) + "\n</team-update>"


async def consume_lead_mail(team_mgr, runtime, lead_mail_event: asyncio.Event) -> None:
    """每秒轮询所有 Team 的 Lead mailbox；非空 → reminder + event.set()（F41a）。"""
    while True:
        try:
            msgs = await team_mgr.poll_lead_mailboxes()
            if msgs:
                if runtime is not None:
                    runtime.append_reminders([build_team_update_reminder(msgs)])
                lead_mail_event.set()
        except Exception:  # noqa: BLE001,S110 轮询失败不崩溃，下一轮重试
            pass
        await asyncio.sleep(1.0)
