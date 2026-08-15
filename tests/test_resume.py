"""TUI 会话恢复单测：列表项展示、do_resume 降级分支、do_resume_session 恢复流程（F18/F21/F22）。"""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from rich.console import Console

from novacode import session
from novacode.conversation import Conversation
from novacode.llm import Message, ToolCall, ToolResult
from novacode.permission import Mode, new_engine
from novacode.tool import new_default_registry
from novacode.tui.resume import (
    SessionItem,
    _fmt_duration,
    _fmt_size,
    _relative_time,
    do_resume,
    do_resume_session,
)


def _console() -> Console:
    return Console(file=io.StringIO(), highlight=False, width=80)


def _holder():
    return SimpleNamespace(conv=None, writer=None, ses_ctx=None)


def _runtime():
    return SimpleNamespace(session=None, context_window=200000)


class _RecordingAgent:
    """记录 run_force_compact 调用（压缩分支验证）。"""

    def __init__(self) -> None:
        self.compact_calls = 0

    async def run_force_compact(self, conv, defs):
        self.compact_calls += 1
        return 0, 0


def _ctx(sessions_dir, root, console, holder, runtime, agent=None):
    _, _ = new_engine(str(root))
    return SimpleNamespace(
        sessions_dir=str(sessions_dir),
        root=str(root),
        console=console,
        holder=holder,
        runtime=runtime,
        agent=agent or _RecordingAgent(),
        registry=new_default_registry(),
        mode_box=SimpleNamespace(mode=Mode.DEFAULT),
    )


def _make_session(sessions_dir, sid: str) -> str:
    """创建会话目录并写一条消息，返回目录绝对路径。"""
    sdir = sessions_dir / sid
    sdir.mkdir(parents=True)
    w = session.Writer(str(sdir))
    w.set_model("deepseek-v4-flash")
    w.on_append(Message(role="user", content="你好"))
    w.on_append(Message(role="assistant", content="嗨"))
    w.close()
    return str(sdir)


# ─────────────────────────── 展示辅助 ───────────────────────────


def test_display_text_fields():
    """F20：display_text 含标题、相对时间、模型、大小。"""
    now = datetime.now().astimezone()
    info = session.SessionInfo(
        id="20260809-100000-aaaa",
        title="第一个会话",
        modified_at=now - timedelta(hours=3),
        model="deepseek-v4-flash",
        size=1536,
        dir="/x",
    )
    text = SessionItem(info).display_text
    assert "第一个会话" in text
    assert "3 hours ago" in text
    assert "deepseek-v4-flash" in text
    assert "1.5KB" in text


def test_fmt_helpers():
    assert _fmt_size(500) == "500B"
    assert _fmt_size(1536) == "1.5KB"
    assert _fmt_size(2 * 1024 * 1024) == "2.0MB"
    assert _fmt_duration(timedelta(minutes=90)) == "1 小时"
    assert _fmt_duration(timedelta(days=2)) == "2 天"
    now = datetime.now().astimezone()
    assert _relative_time(now - timedelta(minutes=5), now) == "5 minutes ago"


# ─────────────────────────── do_resume 降级分支 ───────────────────────────


@pytest.mark.asyncio
async def test_do_resume_no_sessions(tmp_path):
    """F18：无有效会话 → 提示并返回，不进入选择界面。"""
    console = _console()
    ctx = _ctx(tmp_path / ".novacode" / "sessions", tmp_path, console, _holder(), _runtime())
    await do_resume(ctx)
    assert "没有可恢复的历史会话" in console.file.getvalue()
    assert ctx.holder.conv is None


@pytest.mark.asyncio
async def test_do_resume_session_empty_msgs(tmp_path):
    """F21：会话无可恢复消息 → 跳过（list_sessions 已过滤空会话，此处直接构造 info）。"""
    console = _console()
    ctx = _ctx(tmp_path / ".novacode" / "sessions", tmp_path, console, _holder(), _runtime())
    info = session.SessionInfo(
        id="20260809-100000-aaaa",
        title="",
        modified_at=datetime.now().astimezone(),
        model="",
        size=0,
        dir=str(tmp_path / "empty_session"),
    )
    await do_resume_session(ctx, info)
    assert ctx.holder.conv is None
    assert "无可恢复消息" in console.file.getvalue()


# ─────────────────────────── do_resume_session 恢复流程 ───────────────────────────


@pytest.mark.asyncio
async def test_do_resume_session_restores_and_switches(tmp_path):
    """F22：恢复后 holder.conv/writer/ses_ctx 替换、runtime.session 更新、提示输出。"""
    sessions = tmp_path / ".novacode" / "sessions"
    sid = "20260809-100000-aaaa"
    _make_session(sessions, sid)
    console = _console()
    holder = _holder()
    runtime = _runtime()
    ctx = _ctx(sessions, tmp_path, console, holder, runtime)
    info = session.list_sessions(str(sessions))[0]
    await do_resume_session(ctx, info)
    assert holder.conv is not None
    assert [m.content for m in holder.conv.messages()] == ["你好", "嗨"]
    assert holder.writer is not None
    assert holder.ses_ctx.session_id == sid
    assert runtime.session.session_id == sid
    assert "已恢复会话" in console.file.getvalue()


@pytest.mark.asyncio
async def test_do_resume_session_time_gap_appends_reminder(tmp_path):
    """F21 第 5 步：最后消息 ts 距当前超 6h → 末尾追加时间跨度提醒。"""
    sessions = tmp_path / ".novacode" / "sessions"
    sid = "20260801-100000-aaaa"  # 会话创建于 8 天前
    sdir = sessions / sid
    sdir.mkdir(parents=True)
    jsonl = sdir / "conversation.jsonl"
    old_ts = int(time.time()) - 7 * 86400
    jsonl.write_text(
        json.dumps({"role": "user", "content": "旧对话", "ts": old_ts}) + "\n",
        encoding="utf-8",
    )
    console = _console()
    holder = _holder()
    ctx = _ctx(sessions, tmp_path, console, holder, _runtime())
    info = session.list_sessions(str(sessions))[0]
    await do_resume_session(ctx, info)
    msgs = holder.conv.messages()
    assert msgs[-1].role == "user"
    assert "已暂停" in msgs[-1].content
    assert "过时" in msgs[-1].content


@pytest.mark.asyncio
async def test_do_resume_session_compacts_over_limit(tmp_path):
    """F21 第 4 步：估算 token 超限 → 触发一次 run_force_compact。"""
    sessions = tmp_path / ".novacode" / "sessions"
    sid = "20260809-100000-aaaa"
    _make_session(sessions, sid)
    console = _console()
    agent = _RecordingAgent()
    holder = _holder()
    runtime = _runtime()
    runtime.context_window = 2000  # 使 limit 恒负，必然触发压缩
    ctx = _ctx(sessions, tmp_path, console, holder, runtime, agent=agent)
    info = session.list_sessions(str(sessions))[0]
    await do_resume_session(ctx, info)
    assert agent.compact_calls == 1
    assert holder.conv is not None  # 压缩不阻断恢复


@pytest.mark.asyncio
async def test_do_resume_session_renders_history(tmp_path):
    """F23：恢复后把历史逐条渲染进终端 scrollback，再显示系统消息。"""
    sessions = tmp_path / ".novacode" / "sessions"
    sid = "20260809-100000-aaaa"
    _make_session(sessions, sid)
    console = _console()
    holder = _holder()
    ctx = _ctx(sessions, tmp_path, console, holder, _runtime())
    info = session.list_sessions(str(sessions))[0]
    await do_resume_session(ctx, info)
    out = console.file.getvalue()
    assert "恢复会话历史" in out  # 历史分隔提示
    assert "你好" in out  # user 文本被打印
    assert "嗨" in out  # assistant 文本被打印
    # 系统消息在历史之后
    assert out.index("恢复会话历史") < out.index("已恢复会话")


@pytest.mark.asyncio
async def test_do_resume_session_renders_tool_history(tmp_path):
    """F23：含工具调用与结果的历史也逐条渲染（tool_line / tool_result_summary）。"""
    sessions = tmp_path / ".novacode" / "sessions"
    sid = "20260809-100000-aaaa"
    sdir = sessions / sid
    sdir.mkdir(parents=True)
    w = session.Writer(str(sdir))
    w.set_model("m")
    w.on_append(Message(role="user", content="读文件"))
    w.on_append(
        Message(
            role="assistant",
            content="好的",
            tool_calls=[ToolCall(id="c1", name="read_file", input='{"path": "x"}')],
        )
    )
    w.on_append(
        Message(
            role="tool",
            tool_results=[ToolResult(tool_call_id="c1", content="文件内容", is_error=False)],
        )
    )
    w.close()
    console = _console()
    holder = _holder()
    ctx = _ctx(sessions, tmp_path, console, holder, _runtime())
    info = session.list_sessions(str(sessions))[0]
    await do_resume_session(ctx, info)
    out = console.file.getvalue()
    assert "read_file" in out  # tool_line 打印工具名
    assert "文件内容" in out  # tool_result_summary 打印结果


@pytest.mark.asyncio
async def test_do_resume_session_deletes_empty_launch_session(tmp_path):
    """问题 3 方案 a：恢复后启动会话若 0 字节空壳则删除；有内容的启动会话保留。"""
    sessions = tmp_path / ".novacode" / "sessions"
    # 启动会话（空壳，0 字节）
    launch_id = "20260809-200000-0000"
    launch_dir = sessions / launch_id
    launch_dir.mkdir(parents=True)
    (launch_dir / "conversation.jsonl").write_text("", encoding="utf-8")
    (launch_dir / "tool-results").mkdir()
    # 被恢复的目标会话（有内容）
    target_id = "20260808-100000-aaaa"
    _make_session(sessions, target_id)
    console = _console()
    holder = _holder()
    launch_writer = session.Writer(str(launch_dir))  # 模拟 cli 注入的启动 writer
    holder.writer = launch_writer
    holder.conv = Conversation()
    runtime = _runtime()
    runtime.session = SimpleNamespace(
        session_id=launch_id,
        session_dir=str(launch_dir),
        spill_dir=str(launch_dir / "tool-results"),
    )
    ctx = _ctx(sessions, tmp_path, console, holder, runtime)
    info = session.list_sessions(str(sessions))[0]
    assert info.id == target_id  # 空壳被过滤，列表只有目标
    await do_resume_session(ctx, info)
    # 空壳启动会话目录被删
    assert not launch_dir.exists()
    # 目标会话保留
    assert (sessions / target_id / "conversation.jsonl").is_file()
    assert holder.writer is not None
    assert runtime.session.session_id == target_id


@pytest.mark.asyncio
async def test_resume_appends_to_same_jsonl(tmp_path):
    """AC18：恢复后新消息追加到同一 JSONL，行数递增。"""
    sessions = tmp_path / ".novacode" / "sessions"
    sid = "20260809-100000-aaaa"
    _make_session(sessions, sid)
    console = _console()
    holder = _holder()
    ctx = _ctx(sessions, tmp_path, console, holder, _runtime())
    info = session.list_sessions(str(sessions))[0]
    await do_resume_session(ctx, info)
    jsonl = sessions / sid / "conversation.jsonl"
    before = len(jsonl.read_text(encoding="utf-8").splitlines())
    holder.conv.add_user("恢复后新消息")  # 触发 on_append → 写盘
    after = len(jsonl.read_text(encoding="utf-8").splitlines())
    assert after == before + 1
