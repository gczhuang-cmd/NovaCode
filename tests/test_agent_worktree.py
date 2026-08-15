"""_execute_with_worktree 单测（ch14 T11，spec F21/F22/AC15/AC16）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from worktree_fixtures import init_repo

from novacode.agent import Agent
from novacode.agent.agent_worktree import _execute_with_worktree, build_worktree_notice
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission import new_engine
from novacode.subagent.definition import Definition, Source
from novacode.tool import new_default_registry
from novacode.worktree import Manager


class ScriptProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.call_count = 0
        self.requests: list[Request] = []

    async def stream(self, req: Request):
        self.call_count += 1
        self.requests.append(req)
        idx = min(self.call_count - 1, len(self.scripts) - 1)
        for ev in self.scripts[idx]:
            yield ev


def _engine(tmp_path):
    engine, err = new_engine(str(tmp_path))
    assert err is None
    return engine


def _defi() -> Definition:
    return Definition(name="wt", description="d", isolation="worktree", source=Source.BUILTIN)


def test_build_worktree_notice():
    notice = build_worktree_notice("/parent", "/worktree")
    assert "<worktree-context>" in notice
    assert "/parent" in notice
    assert "/worktree" in notice


def test_execute_creates_and_cleans_when_unchanged(tmp_path):
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))
    prov = ScriptProvider([[StreamEvent(text="搞定", done=True)]])
    sub = Agent(prov, new_default_registry(), "test", _engine(tmp_path))
    conv = Conversation()
    q: asyncio.Queue = asyncio.Queue(maxsize=16)

    async def go() -> str:
        return await _execute_with_worktree(mgr, _defi(), sub, conv, "任务", q)

    text = asyncio.run(go())
    assert text == "搞定"
    assert "Worktree 保留" not in text
    # 无变更：auto_cleanup 删除临时 worktree
    assert not list(Path(mgr.worktree_dir).iterdir())


def test_execute_writes_isolated_and_keeps(tmp_path):
    """AC16：子 Agent 在 Worktree 内写文件，主目录不受影响；有变更保留。"""
    init_repo(tmp_path)
    mgr = Manager(str(tmp_path))
    prov = ScriptProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write_file",
                            input=json.dumps({"path": "probe.txt", "content": "hi"}),
                        )
                    ],
                    done=True,
                )
            ],
            [StreamEvent(text="写完了", done=True)],
        ]
    )
    sub = Agent(prov, new_default_registry(), "test", _engine(tmp_path), dont_ask=True)
    conv = Conversation()
    q: asyncio.Queue = asyncio.Queue(maxsize=16)

    async def go() -> str:
        return await _execute_with_worktree(mgr, _defi(), sub, conv, "写文件", q)

    text = asyncio.run(go())
    assert "Worktree 保留" in text
    wts = list(Path(mgr.worktree_dir).iterdir())
    assert len(wts) == 1
    probe = wts[0] / "probe.txt"
    assert probe.read_text(encoding="utf-8") == "hi"
    # 相对路径按 worktree ctx cwd 解析 → 主目录没有该文件
    assert not (tmp_path / "probe.txt").exists()
    # worktree notice 注入到子 Agent 的 task 文本
    assert "<worktree-context>" in conv.messages()[0].content
