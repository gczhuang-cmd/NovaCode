"""Team Manager：多 Team 生命周期 + 成员管理（ch15 T3/T4/T30，spec F3-F10/F45/F41a）。

成员操作（add_member / set_member_active / remove_member）定义在 `Team` 类型上
（types.py），本类只负责 Manager 层：create / get / delete / list_ / handle_task_done /
poll_lead_mailboxes。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .persistence import atomic_write_json, read_json, sanitize
from .types import (
    Team,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)


@dataclass
class LeadMessage:
    """Lead 邮箱轮询到的一条队员消息（spec F41a）。"""

    team_name: str
    from_: str
    type: str
    summary: str
    content: str
    ts: int


@dataclass
class Manager:
    """管理单进程内的多个 Team（spec F3/F4）。"""

    home_dir: str = ""
    wt_mgr: Any = None  # worktree.Manager | None
    task_mgr: Any = None  # task.Manager
    registry: Any = None  # team.registry.AgentNameRegistry
    teams: dict[str, Team] = field(default_factory=dict)  # 按 sanitized_name 索引
    _active_team: str = field(default="", repr=False)  # Lead 视角的活跃 Team（最近创建）
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._teams_dir = str(Path(self.home_dir) / ".novacode" / "teams")
        Path(self._teams_dir).mkdir(parents=True, exist_ok=True)
        self._scan_teams()

    def _scan_teams(self) -> None:
        """扫描子目录还原 teams dict；损坏配置 stderr 警告跳过（F64）。"""
        d = Path(self._teams_dir)
        if not d.is_dir():
            return
        for p in sorted(d.iterdir()):
            if not p.is_dir():
                continue
            cfg = p / "config.json"
            if not cfg.is_file():
                continue
            try:
                team = Team.from_dict(read_json(cfg))
            except Exception as exc:  # noqa: BLE001 损坏配置只警告跳过，不阻断启动
                print(f"team: 跳过损坏的团队配置 {p.name}: {exc}", file=sys.stderr)
                continue
            self._fill_paths(team)
            self.teams[team.sanitized_name] = team

    def _fill_paths(self, team: Team) -> None:
        team.config_dir = str(Path(self._teams_dir) / team.sanitized_name)
        team.config_path = str(Path(team.config_dir) / "config.json")
        team.tasks_path = str(Path(team.config_dir) / "tasks.json")
        team.mailbox_dir = str(Path(team.config_dir) / "mailbox")

    # ── 查询 ─────────────────────────────────────────────

    def get(self, name: str) -> Team | None:
        """按原始名（内部 sanitize）查询。"""
        return self.teams.get(sanitize(name))

    def list_(self) -> list[Team]:
        """按创建时间升序返回全部 Team。"""
        return sorted(self.teams.values(), key=lambda t: t.created_at)

    def get_active_team(self) -> Team | None:
        """Lead 视角的活跃 Team（Team 工具无 ctx 时的兜底定位）：最近创建的 Team。"""
        if self._active_team and self._active_team in self.teams:
            return self.teams[self._active_team]
        lst = self.list_()
        return lst[-1] if lst else None

    # ── 生命周期 ─────────────────────────────────────────

    async def create(self, name: str, description: str = "", agent_type: str = "") -> Team:
        """创建 Team（F5）：sanitize → 去重后缀 → detect 后端 → 落盘 → 注册 Lead。"""
        from .backend.detect import detect  # 延迟 import，避免 manager→backend 顶层环

        sanitized = sanitize(name)
        if not sanitized:
            raise ValueError("团队名清洗后为空，无法创建")
        async with self._lock:
            base, n = sanitized, 1
            while base in self.teams:
                n += 1
                base = f"{sanitized}-{n}"
            sanitized = base
            team = Team(
                name=name,
                sanitized_name=sanitized,
                lead_agent_id="lead",
                backend=detect(),
                description=description,
            )
            self._fill_paths(team)
            Path(team.config_dir).mkdir(parents=True, exist_ok=True)
            Path(team.mailbox_dir).mkdir(parents=True, exist_ok=True)
            team.members = [TeammateInfo(name="lead", agent_id="lead", is_active=None)]
            atomic_write_json(team.config_path, team.to_dict())
            self.teams[sanitized] = team
            self._active_team = sanitized
            return team

    async def delete(self, name: str, force: bool = False) -> None:
        """删除 Team（F7/F66）：杀成员后端 → 清 worktree/session → 删目录。"""
        import shutil

        async with self._lock:
            team = self.get(name)
            if team is None:
                raise TeamNotFoundError(f"未找到团队: {name}")
            if not force and any(m.is_active is not False for m in team.members):
                raise TeamHasActiveMembersError(
                    f"团队 {team.sanitized_name} 仍有活跃成员，拒绝删除；加 --force 强制"
                )
            for m in team.members:
                if m.name == "lead":
                    continue
                await self._kill_member(m)
                await self._cleanup_member(m)
            shutil.rmtree(team.config_dir, ignore_errors=True)
            self.teams.pop(team.sanitized_name, None)

    async def _kill_member(self, m: TeammateInfo) -> None:
        """杀成员后端（tmux/iterm2 kill-pane / in-process cancel task）；失败只警告。"""
        try:
            from .backend import new_backend

            backend = new_backend(m.backend_type, task_mgr=self.task_mgr)
            await backend.kill(m.pane_id, m.agent_id)
        except Exception as exc:  # noqa: BLE001 清理 best-effort，失败不中断
            print(f"team: kill member {m.name} 失败: {exc}", file=sys.stderr)

    async def _cleanup_member(self, m: TeammateInfo) -> None:
        """删队员 worktree 与 session 目录（best-effort）。"""
        import shutil

        if self.wt_mgr is not None and m.branch:
            try:
                from ..worktree import ExitOptions

                # 队员 worktree 名 = branch 去前缀后 `/`←`+` 还原（成员名不含 `/`，无歧义）
                name = m.branch.removeprefix("worktree-").replace("+", "/")
                await self.wt_mgr.remove(name, ExitOptions(discard_changes=True))
            except Exception as exc:  # noqa: BLE001
                print(f"team: 删除成员 {m.name} 的 worktree 失败: {exc}", file=sys.stderr)
        if m.session_dir:
            shutil.rmtree(m.session_dir, ignore_errors=True)

    # ── 队员完成通知（T30，spec F45） ─────────────────────

    async def handle_task_done(self, agent_id: str) -> None:
        """task.Manager 完成回调：标 is_active=False + 写 idle 通知给 Lead（F45）。"""
        if self.registry is None:
            return
        name = self.registry.name_of(agent_id)
        if not name:
            return
        for team in self.teams.values():
            m = team.member_by_name(name)
            if m is None:
                continue
            await team.set_member_active(name, False)
            try:
                from .mailbox import Box, Message, MessageType

                box = Box(team.mailbox_dir)
                await box.write(
                    team.lead_agent_id,
                    Message(
                        from_=name,
                        to=team.lead_agent_id,
                        type=MessageType.TEXT,
                        summary=f"{name} idle",
                        content=f"agent {m.agent_id} finished work, available for new tasks",
                    ),
                )
            except Exception as exc:  # noqa: BLE001 通知失败不阻断
                print(f"team: 写 idle 通知失败: {exc}", file=sys.stderr)
            return

    # ── Lead 邮箱轮询（T30b，spec F41a） ──────────────────

    async def poll_lead_mailboxes(self) -> list[LeadMessage]:
        """遍历所有 Team 读 Lead 未读消息，标 read，返回 LeadMessage 列表（F41a）。"""
        out: list[LeadMessage] = []
        for team in self.list_():
            try:
                from .mailbox import Box

                box = Box(team.mailbox_dir)
                indices, msgs = await box.read_unread(team.lead_agent_id)
            except (OSError, ValueError):
                continue
            if not msgs:
                continue
            try:
                await box.mark_read(team.lead_agent_id, indices)
            except (OSError, ValueError):
                pass
            for msg in msgs:
                out.append(
                    LeadMessage(
                        team_name=team.sanitized_name,
                        from_=msg.from_,
                        type=str(msg.type),
                        summary=msg.summary,
                        content=msg.content,
                        ts=msg.timestamp,
                    )
                )
        return out

    # ── TeamHook 实现（T18，spec F25；agent 包通过 Protocol 委托） ──

    async def spawn_teammate(self, req) -> str:
        from .spawn import spawn_teammate

        return await spawn_teammate(self, req)

    def is_teammate_context(self, ctx) -> tuple[str, str, bool]:
        """判断当前 ctx 是否在某队员执行上下文中（拦截嵌套 spawn，F25 step 2）。

        返回 (team_name, backend_type, in_process)；非队员上下文返回 ("", "", False)。
        """
        from ..agent.team_hook import teammate_context_from_ctx

        tc = teammate_context_from_ctx(ctx)
        if tc is None:
            return "", "", False
        in_proc = tc.backend_type == "in-process"
        return tc.team_name, tc.backend_type, in_proc
