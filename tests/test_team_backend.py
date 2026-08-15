"""backend 检测/命令构造/in-process 测试（ch15 T10/T11/T13，spec F14/F15/F18）。"""

from __future__ import annotations

import asyncio

from novacode.team.backend import SpawnRequest, build_member_cmd, new_backend
from novacode.team.backend.detect import detect
from novacode.team.backend.tmux import TmuxBackend
from novacode.team.types import BackendType


def _req(**kw) -> SpawnRequest:
    base = {
        "team_name": "demo",
        "member_name": "alice",
        "agent_id": "agent-abc",
        "worktree_path": "/mnt/d/NovaCode/.novacode/worktrees/team-demo+alice",
        "session_dir": "/mnt/d/NovaCode/.novacode/sessions/s1",
        "agent_type": "general-purpose",
        "model": "",
        "initial_prompt": "do it",
        "plan_mode_required": False,
    }
    base.update(kw)
    return SpawnRequest(**base)


def test_detect_tmux_env(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1,99999,0,0")
    assert detect() == BackendType.TMUX


def test_detect_in_process(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr("novacode.team.backend.detect.shutil.which", lambda name: None)
    assert detect() == BackendType.IN_PROCESS


def test_detect_iterm2(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr(
        "novacode.team.backend.detect.shutil.which",
        lambda name: "/usr/local/bin/it2" if name == "it2" else None,
    )
    assert detect() == BackendType.ITERM2


def test_build_member_cmd():
    cmd = build_member_cmd(_req(), python="python")
    assert "--team-member" in cmd
    assert "--team demo" in cmd
    assert "--member alice" in cmd
    assert "--agent-id agent-abc" in cmd  # F15：--agent-id 必须传
    assert "--worktree" in cmd and "--session-dir" in cmd
    assert "--agent-type general-purpose" in cmd
    assert "initial_prompt" not in cmd  # F13：initial_prompt 走 mailbox，不走命令行


def test_build_member_cmd_plan_flag():
    cmd = build_member_cmd(_req(plan_mode_required=True), python="python")
    assert "--plan-mode" in cmd


class FakeTaskMgr:
    def __init__(self) -> None:
        self.launched: list[tuple] = []
        self.stopped: list[str] = []

    async def launch(self, ag, conv, name, task):
        self.launched.append((name, task))
        return "task_1"

    async def stop(self, task_id):
        self.stopped.append(task_id)


def test_inprocess_spawn_kill():
    async def go():
        tm = FakeTaskMgr()
        b = new_backend(BackendType.IN_PROCESS, task_mgr=tm)
        req = _req(agent_id="", plan_mode_required=False)
        req.sub_agent = object()
        req.conv = object()
        pane_id, agent_id = await b.spawn(req)
        assert pane_id == "" and agent_id == "task_1"
        assert tm.launched == [("alice", "do it")]
        await b.kill("", "task_1")
        assert tm.stopped == ["task_1"]
        await b.wake("", "task_1")  # no-op 不抛

    asyncio.run(go())


def test_new_backend_unknown():
    import pytest

    with pytest.raises(ValueError):
        new_backend("bogus")  # 未知后端类型


def test_tmux_spawn_failure(monkeypatch):
    """tmux split-window 失败（非零退出）→ 抛 RuntimeError（失败回归，不静默回退 in-process）。"""

    class _FakeProc:
        returncode = 1

        async def communicate(self):
            return b"", b"tmux: error opening terminal"

    async def _fake_exec(*args, **kw):
        return _FakeProc()

    monkeypatch.setattr("novacode.team.backend.tmux.asyncio.create_subprocess_exec", _fake_exec)
    b = TmuxBackend()

    async def go():
        try:
            await b.spawn(_req())
            assert False, "should raise RuntimeError"
        except RuntimeError as e:
            assert "tmux spawn 失败" in str(e)

    asyncio.run(go())
