"""session 子包单测：JSONL 写入/compact 标记、列表扫描、加载恢复、过期清理。"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

from novacode.compact.state import new_session_context
from novacode.llm import Message, ToolCall, ToolResult
from novacode.session import Writer, clean_expired, list_sessions, load_session


def _conv(session_dir: str, on_append=None, on_replace=None):
    from novacode.conversation import Conversation

    return Conversation(on_append=on_append, on_replace=on_replace)


# ─────────────────────────── Writer ───────────────────────────


def test_writer_path_attribute(tmp_path):
    """ch10 T0b：writer.path 指向 conversation.jsonl 绝对路径且文件已建。"""
    w = Writer(str(tmp_path))
    assert w.path == str(tmp_path / "conversation.jsonl")
    assert Path(w.path).is_file()
    w.close()


def test_writer_open_existing_path_attribute(tmp_path):
    """ch10 T0b：open_existing 同样带 path 属性。"""
    sdir = tmp_path / "s"
    sdir.mkdir()
    w = Writer(str(sdir))
    w.close()
    w2 = Writer.open_existing(str(sdir))
    assert w2.path == str(sdir / "conversation.jsonl")
    w2.close()


def test_writer_append_and_read(tmp_path):
    """T8-1：写入 3 条消息 → 逐行读回验证 JSON 结构（role/content/ts，首行带 model）。"""
    writer = Writer(str(tmp_path / "s"))
    writer.set_model("deepseek-v4-flash")
    conv = _conv(str(tmp_path / "s"), writer.on_append, writer.on_replace)
    conv.add_user("hello")
    conv.add_assistant("hi!")
    conv.add_user("again")
    writer.close()
    lines = (tmp_path / "s" / "conversation.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["role"] == "user" and first["content"] == "hello"
    assert first["model"] == "deepseek-v4-flash"  # 首行带 model（F11）
    assert isinstance(first["ts"], int)
    second = json.loads(lines[1])
    assert second["role"] == "assistant" and second["content"] == "hi!"
    assert second.get("model") is None  # 非首条不写 model
    assert json.loads(lines[2])["role"] == "user"


def test_writer_tool_roundtrip(tmp_path):
    """T8：tool_calls / tool_results 序列化后可还原。"""
    writer = Writer(str(tmp_path / "s"))
    writer.set_model("m")
    conv = _conv(str(tmp_path / "s"), writer.on_append, writer.on_replace)
    conv.add_user("读")
    conv.add_assistant_with_tool_calls(
        "好", [ToolCall(id="c1", name="read_file", input='{"path": "x"}')]
    )
    conv.add_tool_results([ToolResult(tool_call_id="c1", content="内容", is_error=False)])
    writer.close()
    msgs, _ = load_session(str(tmp_path / "s"))
    assert len(msgs) == 3
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].tool_results[0].content == "内容"


def test_writer_compact_marker_and_load_from_last(tmp_path):
    """T8-2：消息 → compact 标记 → 新消息 → load_session 只返回 compact 后的。"""
    writer = Writer(str(tmp_path / "s"))
    writer.set_model("m")
    conv = _conv(str(tmp_path / "s"), writer.on_append, writer.on_replace)
    conv.add_user("u1")
    conv.add_assistant("a1")
    # 触发 on_replace → compact 标记 + 全量追加
    conv.replace_history([Message(role="user", content="sum")])
    conv.add_user("u2")
    writer.close()
    lines = (tmp_path / "s" / "conversation.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("compact" in l for l in lines)  # compact 标记写入
    msgs, last_ts = load_session(str(tmp_path / "s"))
    # 从最后一个 compact 标记后加载：sum / u2
    assert [m.content for m in msgs] == ["sum", "u2"]
    assert isinstance(last_ts, int)


def test_writer_open_existing_appends(tmp_path):
    """T8-6（恢复后追加）：open_existing 打开已有文件 → 新消息追加到同一 JSONL。"""
    d = tmp_path / "s"
    writer = Writer(str(d))
    writer.set_model("m")
    writer.on_append(Message(role="user", content="old"))
    writer.close()
    n_before = len((d / "conversation.jsonl").read_text(encoding="utf-8").splitlines())
    w2 = Writer.open_existing(str(d))
    w2.on_append(Message(role="user", content="new"))
    w2.close()
    lines = (d / "conversation.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_before + 1
    assert json.loads(lines[-1])["content"] == "new"
    assert json.loads(lines[-1]).get("model") is None  # 已有内容的文件不再写 model


# ─────────────────────────── 加载恢复 ───────────────────────────


def test_load_session_bad_line_skip(tmp_path):
    """AC14：中间插入坏行 → 恢复时跳过，其余正常。"""
    d = tmp_path / "s"
    writer = Writer(str(d))
    writer.set_model("m")
    conv = _conv(str(d), writer.on_append, writer.on_replace)
    conv.add_user("u1")
    conv.add_assistant("a1")
    writer.close()
    jsonl = d / "conversation.jsonl"
    # 手动插入坏行
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write("{this is bad json\n")
        f.write('{"role": "user", "content": "u2", "ts": 1}\n')
    msgs, _ = load_session(str(d))
    assert [m.content for m in msgs] == ["u1", "a1", "u2"]  # 坏行被跳过


def test_load_session_orphaned_tool_calls_truncated(tmp_path):
    """AC15：末尾是带 tool_calls 的 assistant → 恢复时截断。"""
    d = tmp_path / "s"
    writer = Writer(str(d))
    writer.set_model("m")
    conv = _conv(str(d), writer.on_append, writer.on_replace)
    conv.add_user("u")
    conv.add_assistant("正常回复")
    conv.add_assistant_with_tool_calls("调用", [ToolCall(id="c1", name="read_file", input="{}")])
    writer.close()
    msgs, _ = load_session(str(d))
    assert len(msgs) == 2
    assert msgs[-1].content == "正常回复"
    assert not (msgs[-1].tool_calls)


def test_load_session_missing_file_returns_empty(tmp_path):
    msgs, last_ts = load_session(str(tmp_path / "nope"))
    assert msgs == [] and last_ts is None


# ─────────────────────────── 列表扫描 ───────────────────────────


def test_list_sessions_sorted_and_meta(tmp_path):
    """AC12：3 个有效会话 → 列表 3 项，按修改时间倒序，含标题/模型/大小。"""
    # 手动构造两个会话：一个较新带 model，一个较旧
    d1 = tmp_path / "sessions" / "20260809-100000-aaaa"
    d1.mkdir(parents=True)
    w = Writer(str(d1))
    w.set_model("deepseek-v4-flash")
    w.on_append(Message(role="user", content="第一个会话标题"))
    w.close()

    d2 = tmp_path / "sessions" / "20260801-100000-bbbb"
    d2.mkdir(parents=True)
    w2 = Writer(str(d2))
    w2.on_append(Message(role="user", content="第二个"))
    w2.close()
    # 调整 mtime：d1 新、d2 旧
    old = time.time() - 86400
    import os

    os.utime(d2 / "conversation.jsonl", (old, old))

    items = list_sessions(str(tmp_path / "sessions"))
    assert len(items) == 2
    assert items[0].id.startswith("20260809")  # 最新在前
    assert items[0].title == "第一个会话标题"
    assert items[0].model == "deepseek-v4-flash"
    assert items[0].size > 0


def test_list_sessions_skips_old_format_and_no_jsonl(tmp_path):
    """AC20/N3：旧格式目录与无 JSONL 目录不展示。"""
    sessions = tmp_path / "sessions"
    # 新格式但无 conversation.jsonl
    (sessions / "20260801-000000-0000").mkdir(parents=True)
    # 旧格式带 JSONL
    old = sessions / "1717000000-abc12345"
    old.mkdir(parents=True)
    (old / "conversation.jsonl").write_text(
        '{"role":"user","content":"x","ts":1}\n', encoding="utf-8"
    )
    assert list_sessions(str(sessions)) == []


def test_list_sessions_skips_empty_jsonl(tmp_path):
    """空会话（JSONL 0 字节）不展示，避免 /resume 列表出现无内容条目。"""
    sessions = tmp_path / "sessions"
    d = sessions / "20260801-000000-0000"
    d.mkdir(parents=True)
    (d / "conversation.jsonl").write_text("", encoding="utf-8")
    assert list_sessions(str(sessions)) == []


def test_relative_time():
    """F20：相对时间描述。"""
    from datetime import datetime, timedelta

    from novacode.session.list import _relative_time

    now = datetime.now().astimezone()
    assert _relative_time(now, now) == "just now"
    assert _relative_time(now - timedelta(minutes=5), now) == "5 minutes ago"
    assert _relative_time(now - timedelta(hours=3), now) == "3 hours ago"
    assert _relative_time(now - timedelta(days=1), now) == "1 days ago"


# ─────────────────────────── 过期清理 ───────────────────────────


def test_clean_expired_only_old_new_format(tmp_path):
    """AC19/AC20：只删超过 max_age 的新格式目录；旧格式保留；新会话保留。"""
    sessions = tmp_path / "sessions"
    old_new = sessions / "20260601-100000-0000"  # 超过 30 天（当前 2026-08）
    old_new.mkdir(parents=True)
    (old_new / "conversation.jsonl").write_text("x\n", encoding="utf-8")
    recent = sessions / "20260808-100000-1111"
    recent.mkdir(parents=True)
    (recent / "conversation.jsonl").write_text("x\n", encoding="utf-8")
    legacy = sessions / "1717000000-abc12345"
    legacy.mkdir(parents=True)
    (legacy / "conversation.jsonl").write_text("x\n", encoding="utf-8")

    clean_expired(str(sessions), timedelta(days=30))
    assert not old_new.exists()  # 过期新格式被删
    assert recent.exists()  # 新会话保留
    assert legacy.exists()  # 旧格式不清理（N3）


def test_clean_expired_handles_missing_dir(tmp_path):
    """sessions_dir 不存在 → 不报错。"""
    clean_expired(str(tmp_path / "nope"), timedelta(days=30))


def test_new_session_context_supports_writer(tmp_path):
    """T1+T4 集成：new_session_context 的 session_dir 直接喂 Writer。"""
    sctx = new_session_context(str(tmp_path))
    writer = Writer(sctx.session_dir)
    writer.set_model("m")
    writer.on_append(Message(role="user", content="hi"))
    writer.close()
    assert (Path(sctx.session_dir) / "conversation.jsonl").is_file()
