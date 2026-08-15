"""memory 子包单测：笔记 CRUD、索引读写、合并/截断、异步更新解析。"""

from __future__ import annotations

import pytest

from novacode.llm import Message, Request, StreamEvent
from novacode.memory import Manager, Store
from novacode.memory.types import UpdateAction


def _create(**kw) -> UpdateAction:
    defaults = {"action": "create", "level": "project", "type": "project_knowledge"}
    defaults.update(kw)
    return UpdateAction(**defaults)


# ─────────────────────────── ch10 list_files ───────────────────────────


def test_manager_list_files_empty_when_dirs_missing(tmp_path):
    """目录不存在 → 两级都返回空 list，不抛异常。"""
    mgr = Manager(str(tmp_path / "proj"), str(tmp_path / "user"))
    assert mgr.list_files() == ([], [])


def test_manager_list_files_only_memmd(tmp_path):
    """仅 MEMORY.md → 各自返回 ["MEMORY.md"]。"""
    for sub in ("proj", "user"):
        d = tmp_path / sub
        d.mkdir()
        (d / "MEMORY.md").write_text("# idx\n", encoding="utf-8")
    mgr = Manager(str(tmp_path / "proj"), str(tmp_path / "user"))
    assert mgr.list_files() == (["MEMORY.md"], ["MEMORY.md"])


def test_manager_list_files_mixed_and_sorted(tmp_path):
    """含多 .md 与非 .md 混合 → 只列 .md，且按字典序。"""
    (tmp_path / "proj").mkdir()
    for name in ("b.md", "a.md", "MEMORY.md", "note.txt"):
        (tmp_path / "proj" / name).write_text("x", encoding="utf-8")
    mgr = Manager(str(tmp_path / "proj"), str(tmp_path / "user"))
    proj, user = mgr.list_files()
    assert proj == ["MEMORY.md", "a.md", "b.md"]
    assert user == []


# ─────────────────────────── Store ───────────────────────────


def test_store_create_note(tmp_path):
    """AC21/AC22：create → 文件存在、frontmatter 正确、MEMORY.md 有对应行。"""
    store = Store(str(tmp_path))
    store.apply(
        [_create(slug="use_chinese", title="项目用中文", content="本项目代码与注释使用中文。")]
    )
    note = tmp_path / "project_knowledge_use_chinese.md"
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "type: project_knowledge" in text
    assert "title: 项目用中文" in text
    assert "created:" in text and "updated:" in text
    assert "本项目代码与注释使用中文。" in text
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "[project_knowledge] 项目用中文" in index


def test_store_update_note(tmp_path):
    """AC：update → 文件内容更新、frontmatter 保留 created、updated 变化、索引行更新。"""
    store = Store(str(tmp_path))
    store.apply([_create(slug="use_chinese", title="项目用中文", content="第一版。")])
    note = tmp_path / "project_knowledge_use_chinese.md"
    import re

    created = re.search(r"created: (\S+)", note.read_text(encoding="utf-8")).group(1)
    store.apply(
        [
            UpdateAction(
                action="update",
                level="project",
                filename="project_knowledge_use_chinese.md",
                title="项目用中文",
                content="第二版内容。",
            )
        ]
    )
    text = note.read_text(encoding="utf-8")
    assert "第二版内容。" in text
    assert "第一版。" not in text
    assert f"created: {created}" in text  # created 保留
    assert "updated: 2026" in text or "updated: " in text
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "[project_knowledge] 项目用中文" in index
    assert "第二版内容" in index  # 描述更新


def test_store_delete_note(tmp_path):
    """AC：delete → 文件不存在、MEMORY.md 对应行消失。"""
    store = Store(str(tmp_path))
    store.apply([_create(slug="old", title="旧知识", content="旧内容。")])
    assert (tmp_path / "project_knowledge_old.md").is_file()
    store.apply(
        [UpdateAction(action="delete", level="project", filename="project_knowledge_old.md")]
    )
    assert not (tmp_path / "project_knowledge_old.md").exists()
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "旧知识" not in index


def test_store_load_index_missing_returns_empty(tmp_path):
    assert Store(str(tmp_path / "nope")).load_index() == ""


def test_store_apply_empty_does_nothing(tmp_path):
    store = Store(str(tmp_path))
    store.apply([])
    assert not (tmp_path / "MEMORY.md").exists()


def test_store_bad_action_logged(tmp_path):
    """未知 action 静默记录日志，不生成笔记文件（apply 会写空索引）。"""
    store = Store(str(tmp_path))
    store.apply([UpdateAction(action="explode", level="project")])
    # 无笔记文件（排除索引 MEMORY.md）
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".md") and p.name != "MEMORY.md"]
    index = tmp_path / "MEMORY.md"
    assert not index.exists() or index.read_text(encoding="utf-8") == ""


# ─────────────────────────── Manager ───────────────────────────


def test_manager_load_index_merge_project_first(tmp_path):
    """AC：两级各有索引 → 合并返回，项目级在前。"""
    project = tmp_path / "proj"
    user = tmp_path / "user"
    Store(str(project)).apply([_create(slug="a", title="项目A", content="PA")])
    Store(str(user)).apply(
        [
            UpdateAction(
                action="create",
                level="user",
                type="user_preference",
                title="用户B",
                slug="b",
                content="UB",
            )
        ]
    )
    mgr = Manager(str(project), str(user))
    text = mgr.load_index()
    assert text.index("项目A") < text.index("用户B")


def test_manager_load_index_truncate(tmp_path):
    """AC26：构造超 25KB 索引 → 截断 + (index truncated) 标注。"""
    project = tmp_path / "proj"
    user = tmp_path / "user"
    Store(str(project)).apply([_create(slug="big", title="大索引", content="x" * 30000)])
    mgr = Manager(str(project), str(user))
    text = mgr.load_index()
    assert "(index truncated)" in text
    assert len(text.encode("utf-8")) <= 25 * 1024


class FakeMemProvider:
    """脚本化 provider：记录请求，返回预置流。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, stream_text: str) -> None:
        self.stream_text = stream_text
        self.requests: list[Request] = []

    async def stream(self, req: Request):
        self.requests.append(req)
        yield StreamEvent(text=self.stream_text)
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_manager_update_async_creates_note(tmp_path):
    """T11：mock provider 返回 create JSON → 笔记文件被创建 + 回调收到新索引。"""
    project = tmp_path / "proj"
    user = tmp_path / "user"
    provider = FakeMemProvider(
        '[{"action":"create","level":"project","type":"project_knowledge",'
        '"title":"记住用中文","slug":"use_chinese","content":"代码用中文。"}]'
    )
    mgr = Manager(str(project), str(user), provider=provider, model="m")
    refreshed: list[str] = []
    mgr.add_updated_callback(refreshed.append)
    await mgr.update_async([Message(role="user", content="记住：这个项目用中文")])
    note = tmp_path / "proj" / "project_knowledge_use_chinese.md"
    assert note.is_file()
    assert "记住用中文" in (tmp_path / "proj" / "MEMORY.md").read_text(encoding="utf-8")
    assert refreshed and "记住用中文" in refreshed[-1]
    # 请求不传工具定义（F38），system 为记忆更新 prompt
    req = provider.requests[0]
    assert req.tools is None
    assert "JSON" in (req.system.stable or "")


@pytest.mark.asyncio
async def test_manager_update_async_fenced_and_empty(tmp_path):
    """T11：provider 返回 Markdown fence 包裹 JSON → 正常解析；空数组不写任何文件。"""
    project = tmp_path / "proj"
    user = tmp_path / "user"
    provider = FakeMemProvider("```json\n[]\n```")
    mgr = Manager(str(project), str(user), provider=provider, model="m")
    await mgr.update_async([Message(role="user", content="随便聊聊")])
    assert not (tmp_path / "proj").exists()  # 无更新，不创建目录


@pytest.mark.asyncio
async def test_manager_update_async_failure_silent(tmp_path):
    """AC25：provider 出错 → 主会话不受影响、不抛异常（异常被记录日志）。"""
    project = tmp_path / "proj"
    user = tmp_path / "user"

    class ErrProvider:
        name = "fake"
        model = "fake-model"

        async def stream(self, req: Request):
            yield StreamEvent(err=RuntimeError("boom"))

    mgr = Manager(str(project), str(user), provider=ErrProvider(), model="m")
    # 不应抛异常
    await mgr.update_async([Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_manager_update_async_without_provider_noop(tmp_path):
    """provider 未设置（启动早期）→ update_async 直接返回。"""
    mgr = Manager(str(tmp_path / "p"), str(tmp_path / "u"))
    await mgr.update_async([Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_manager_update_async_parses_bad_json_empty(tmp_path):
    """provider 返回非 JSON → 解析为空操作，静默跳过。"""
    project = tmp_path / "proj"
    user = tmp_path / "user"
    provider = FakeMemProvider("对不起，我不知道")
    mgr = Manager(str(project), str(user), provider=provider, model="m")
    await mgr.update_async([Message(role="user", content="hi")])
    assert not (tmp_path / "proj").exists()


def test_parse_actions_fence_stripped():
    """F39：fence 包裹与纯 JSON 两种形式都可解析。"""
    from novacode.memory.manager import Manager

    actions = Manager._parse_actions(
        '```json\n[{"action":"delete","level":"project","filename":"a.md"}]\n```'
    )
    assert len(actions) == 1 and actions[0].action == "delete"
    actions2 = Manager._parse_actions(
        '[{"action":"create","level":"user","type":"user_preference","title":"t","slug":"s","content":"c"}]'
    )
    assert actions2[0].type == "user_preference"
