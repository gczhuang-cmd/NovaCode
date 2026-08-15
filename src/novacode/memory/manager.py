"""记忆管理器：编排项目级/用户级笔记加载与异步 LLM 更新（ch09 F32~F42）。

- `load_index`：合并两级索引注入系统提示（截断 25KB，F34）；
- `update_async`：独立 asyncio task 中调用，不阻塞主会话（F36）；失败静默记录日志（F42）；
- 更新成功落盘后通知回调刷新 Agent 的记忆文本快照（F32）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..llm import Message, Request, System
from .prompts import MEMORY_UPDATE_SYSTEM_PROMPT
from .store import Store
from .types import UpdateAction

if TYPE_CHECKING:
    from ..llm import Provider

logger = logging.getLogger(__name__)

# 注入前索引拼接的总大小上限（F34）
MAX_INDEX_BYTES = 25 * 1024


class Manager:
    """编排项目级和用户级笔记的加载和更新。"""

    def __init__(
        self,
        project_dir: str,
        user_dir: str,
        provider: Provider | None = None,
        model: str = "",
    ) -> None:
        self._project = Store(project_dir)
        self._user = Store(user_dir)
        self._provider = provider
        self._model = model
        self._lock = asyncio.Lock()  # 防并发更新（N2）
        self._on_updated: list[Callable[[str], None]] = []  # 索引刷新通知（F32）
        self._pending: set[asyncio.Task] = set()  # 未完成更新 task（退出收尾，R12）

    def add_updated_callback(self, cb: Callable[[str], None]) -> None:
        """注册「记忆索引已刷新」回调（Agent 更新 memory_text 快照用）。"""
        self._on_updated.append(cb)

    def launch(self, recent_msgs: list[Message]) -> asyncio.Task | None:
        """创建并追踪一次异步记忆更新 task（F36）。

        Agent 用 `launch` 而非裸 `asyncio.create_task`，以便进程退出时
        `wait_pending()` 统一收尾（R12）。provider 未设置时返回 None。
        """
        if self._provider is None:
            return None
        task = asyncio.create_task(self.update_async(recent_msgs))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def wait_pending(self) -> None:
        """等待全部未完成记忆更新 task（进程退出收尾，F36/R12）。"""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    def set_provider(self, provider: Provider, model: str) -> None:
        """延迟设置 provider（启动时 provider 未选定，T16）。"""
        self._provider = provider
        self._model = model

    def list_files(self) -> tuple[list[str], list[str]]:
        """列出项目层与用户层已加载记忆文件名（含 MEMORY.md），各自按字典序（ch10 T0a）。

        /memory 纯本地查询的数据源；目录不存在/出错返回空 list，不抛异常。
        """
        return (self._project.list_files(), self._user.list_files())

    def load_index(self) -> str:
        """合并两级索引（项目级在前、用户级在后），超 25KB 截断并标注（F32~F34）。

        按**字节**截断（UTF-8 中文 3 字节/字符），保证截断后总字节不超上限。
        """
        parts = [self._project.load_index(), self._user.load_index()]
        text = "\n".join(p for p in parts if p.strip())
        if len(text.encode("utf-8")) > MAX_INDEX_BYTES:
            mark = "\n(index truncated)"
            cutoff = MAX_INDEX_BYTES - len(mark.encode("utf-8"))
            text = text.encode("utf-8")[:cutoff].decode("utf-8", errors="ignore") + mark
        return text

    async def update_async(self, recent_msgs: list[Message]) -> None:
        """异步发起记忆更新（F36/F42）；provider 未设置时直接返回。"""
        if self._provider is None:
            return
        async with self._lock:
            try:
                req_text = self._build_request_text(recent_msgs)
                req = Request(
                    messages=[Message(role="user", content=req_text)],
                    tools=None,  # 不传工具定义（F38）
                    system=System(stable=MEMORY_UPDATE_SYSTEM_PROMPT),
                )
                buf: list[str] = []
                async for ev in self._provider.stream(req):
                    if ev.err is not None:
                        raise ev.err
                    if ev.text:
                        buf.append(ev.text)
                actions = self._parse_actions("".join(buf))
                if not actions:
                    return
                project_actions = [a for a in actions if a.level == "project"]
                user_actions = [a for a in actions if a.level == "user"]
                if project_actions:
                    self._project.apply(project_actions)
                if user_actions:
                    self._user.apply(user_actions)
                # 索引已变 → 通知 Agent 刷新 memory_text 快照（F32）
                refreshed = self.load_index()
                for cb in self._on_updated:
                    try:
                        cb(refreshed)
                    except Exception:
                        logger.exception("记忆索引刷新回调失败")
            except Exception:
                logger.exception("记忆更新失败")

    def _build_request_text(self, recent_msgs: list[Message]) -> str:
        """把最近消息 + 两级现有索引打包成一条 user 消息文本（F37）。"""
        lines = ["<recent_conversation>"]
        for m in recent_msgs:
            if m.content:
                lines.append(f"[{m.role}] {m.content}")
            for tc in m.tool_calls:
                lines.append(f"[assistant tool_call] {tc.name} {tc.input}")
            for tr in m.tool_results:
                lines.append(f"[tool_result] {tr.content[:500]}")
        lines.append("</recent_conversation>")
        lines.append("<current_index>")
        index = self.load_index()
        lines.append(index if index else "(空)")
        lines.append("</current_index>")
        lines.append("请基于以上内容输出 JSON 操作数组。")
        return "\n".join(lines)

    @staticmethod
    def _parse_actions(raw: str) -> list[UpdateAction]:
        """剥离 Markdown fence 后解析 JSON 数组为 UpdateAction 列表（F39）。"""
        text = raw.strip()
        # 剥离 ```json ... ``` 围栏
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        if not text:
            return []
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        actions: list[UpdateAction] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            actions.append(
                UpdateAction(
                    action=str(item.get("action") or ""),
                    level=str(item.get("level") or ""),
                    type=str(item.get("type") or ""),
                    title=str(item.get("title") or ""),
                    slug=str(item.get("slug") or ""),
                    content=str(item.get("content") or ""),
                    filename=str(item.get("filename") or ""),
                )
            )
        return actions
