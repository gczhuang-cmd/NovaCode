"""TeamSendMessage 工具（ch15 T22/T31，spec F31-F34/F46/F47）。

写 mailbox + wake Pane 后端 + in-process 已 stop 时续派（复用 task.Manager.send_message）。
"""

from __future__ import annotations

import json
import time

from ...tool import Result
from ..mailbox import Box, Message, MessageType
from ..types import Team
from ._common import resolve_team


class TeamSendMessageTool:
    """给队友发消息（name / agent_id / "*" 广播）。"""

    read_only = False
    is_system = False

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamSendMessage"

    def description(self) -> str:
        return (
            "给当前 Team 的队员发消息（to 支持队员名 / agent_id / '*' 广播）。"
            "团队协作、队员通信、Plan 审批都用本工具；不要用后台任务的 SendMessage。"
            "type 可选 text/shutdown_request/shutdown_response/plan_approval_response。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "队员名 / agent_id / '*' 广播"},
                "summary": {"type": "string", "description": "纯文本消息必填，5-10 词摘要"},
                "message": {"type": "string", "description": "纯文本消息体（可选）"},
                "type": {
                    "type": "string",
                    "description": "text（默认）/ shutdown_request / shutdown_response / plan_approval_response",
                },
                "payload": {
                    "type": "object",
                    "description": "结构化消息载荷（如 {approve, feedback}）",
                },
            },
            "required": ["to"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        if not isinstance(data, dict):
            return Result(is_error=True, content="TeamSendMessage 参数应为 JSON 对象")
        to = str(data.get("to") or "").strip()
        if not to:
            return Result(is_error=True, content="to 必填")
        mtype_str = str(data.get("type") or "text").strip()
        try:
            msg_type = MessageType(mtype_str)
        except ValueError:
            return Result(is_error=True, content=f"非法消息类型: {mtype_str}")
        summary = str(data.get("summary") or "")
        message = str(data.get("message") or "")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else None

        try:
            team = resolve_team(self._mgr)
            return await self._deliver(team, to, msg_type, summary, message, payload)
        except Exception as e:  # noqa: BLE001 结构化错误返回
            return Result(is_error=True, content=str(e))

    # ── 内部 ─────────────────────────────────────────────

    def _caller_name(self, team: Team) -> str:
        from ...agent.team_hook import teammate_context_from_ctx

        tc = teammate_context_from_ctx(None)
        if tc is not None and tc.member_name:
            return tc.member_name
        return "lead"

    async def _deliver(self, team, to, msg_type, summary, message, payload) -> Result:
        from ...agent.team_hook import teammate_context_from_ctx
        from ..backend import new_backend

        tc = teammate_context_from_ctx(None)
        is_lead = tc is None
        # 权限校验（F34 step 3）
        if msg_type == MessageType.PLAN_APPROVAL_RESPONSE and not is_lead:
            return Result(is_error=True, content="plan_approval_response 仅 Lead 可发")
        if msg_type == MessageType.SHUTDOWN_RESPONSE and to != team.lead_agent_id:
            return Result(is_error=True, content="shutdown_response 只能发给 Lead")

        caller = self._caller_name(team)
        targets: list = []
        if to == "*":
            targets = [m for m in team.members if m.name not in (caller, "lead")]
        else:
            agent_id = self._mgr.registry.resolve(to) if self._mgr.registry is not None else None
            if agent_id is None:
                agent_id = to
            m = team.member_by_agent_id(agent_id)
            if m is None:
                m = team.member_by_name(to)
            if m is None:
                return Result(is_error=True, content=f"找不到目标成员: {to}")
            targets = [m]
        if not targets:
            return Result(is_error=True, content="没有可投递的成员")

        box = Box(team.mailbox_dir)
        delivered: list[str] = []
        for m in targets:
            await box.write(
                m.agent_id,
                Message(
                    from_=caller,
                    to=m.name,
                    type=msg_type,
                    summary=summary,
                    content=message,
                    payload=payload,
                ),
            )
            # Pane 后端唤醒（F34 step 6）
            if str(m.backend_type) != "in-process":
                backend = new_backend(m.backend_type, task_mgr=self._mgr.task_mgr)
                await backend.wake(m.pane_id, m.agent_id)
            # in-process 已 stop → 续派（F46/T31）
            if str(m.backend_type) == "in-process" and self._mgr.task_mgr is not None:
                bt = self._mgr.task_mgr.get(m.agent_id)
                if bt is not None and bt.status.value != 0:  # Status.RUNNING == 0
                    await team.set_member_active(m.name, True)
                    try:
                        await self._mgr.task_mgr.send_message(m.name, message or summary)
                    except Exception as e:  # noqa: BLE001 续派失败不阻断投递
                        return Result(is_error=True, content=f"消息已投递但续派失败: {e}")
            delivered.append(m.agent_id)
        return Result(
            content=json.dumps({"delivered_to": delivered, "timestamp": int(time.time())})
        )
