"""单级笔记存储：笔记文件 CRUD + MEMORY.md 索引（ch09 F28~F31/F40）。

Store 管理单级（项目级或用户级）的笔记文件与索引。文件操作由
`threading.Lock` 保护，防止两次连续更新的读-写冲突（N2）。
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path

import yaml

from .types import NoteType, UpdateAction

logger = logging.getLogger(__name__)

# 索引行格式：- [<type>] <title> — <一句话描述>（F30）
_INDEX_LINE = "- [{type}] {title} — {desc}"


def _read_frontmatter(path: Path) -> dict:
    """读取笔记文件的 `---` frontmatter 块，解析为 dict；格式异常返回 {}。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except Exception:  # noqa: BLE001 frontmatter 异常降级为空
        return {}
    return data if isinstance(data, dict) else {}


def _body(path: Path) -> str:
    """读取笔记正文（去掉 frontmatter 块）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def _first_line(text: str) -> str:
    """索引描述取正文首行非空行。"""
    for ln in text.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


class Store:
    """管理单级（项目级或用户级）的笔记文件和索引。"""

    def __init__(self, dir: str) -> None:
        self._dir = Path(dir)
        self._lock = threading.Lock()

    @property
    def dir(self) -> str:
        """笔记目录绝对路径（供测试/展示）。"""
        return str(self._dir)

    def ensure_dir(self) -> None:
        """创建笔记目录（不存在时）。"""
        os.makedirs(self._dir, exist_ok=True)

    def _index_path(self) -> Path:
        return self._dir / "MEMORY.md"

    def load_index(self) -> str:
        """读取 MEMORY.md 内容；不存在返回空字符串（F30）。"""
        try:
            return self._index_path().read_text(encoding="utf-8")
        except OSError:
            return ""

    def list_files(self) -> list[str]:
        """列出本层 memory 目录下的 `.md` 文件名（含 MEMORY.md 自身），按字典序（ch10）。

        目录不存在视为空 list，不抛异常；其他 OSError 记 warning 后视为空 list。
        """
        try:
            return sorted(p.name for p in self._dir.glob("*.md") if p.is_file())
        except OSError as e:
            logger.warning("列出记忆文件失败: %s", e)
            return []

    def apply(self, actions: list[UpdateAction]) -> None:
        """执行 create / update / delete 并同步索引（F40）。单个失败记日志不影响其他。"""
        if not actions:
            return
        self.ensure_dir()
        with self._lock:
            index = self._load_index_lines()
            for act in actions:
                try:
                    self._apply_one(act, index)
                except OSError as e:
                    logger.warning("笔记操作失败 %s(%s): %s", act.action, act.filename, e)
            self._write_index(index)

    def _apply_one(self, act: UpdateAction, index: list[str]) -> None:
        if act.action == "create":
            self._create(act, index)
        elif act.action == "update":
            self._update(act, index)
        elif act.action == "delete":
            self._delete(act, index)
        else:
            logger.warning("未知笔记操作: %s", act.action)

    def _load_index_lines(self) -> list[str]:
        try:
            return self._index_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def _write_index(self, lines: list[str]) -> None:
        text = "\n".join(lines)
        if text:
            text += "\n"
        self._index_path().write_text(text, encoding="utf-8")

    def _write_note(
        self, fname: str, type_: str, title: str, created: str, updated: str, body: str
    ) -> None:
        """写笔记文件：yaml.safe_dump 生成 frontmatter + 正文（F28）。"""
        fm = yaml.safe_dump(
            {"type": type_, "title": title, "created": created, "updated": updated},
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        text = f"---\n{fm}\n---\n"
        if body:
            text += body if body.endswith("\n") else body + "\n"
        (self._dir / fname).write_text(text, encoding="utf-8")

    def _create(self, act: UpdateAction, index: list[str]) -> None:
        try:
            note_type = NoteType(act.type)
        except ValueError:
            note_type = NoteType.PROJECT_KNOWLEDGE
        now = datetime.now().astimezone().isoformat()
        fname = f"{note_type.value}_{act.slug}.md"
        body = act.content or ""
        self._write_note(fname, note_type.value, act.title, now, now, body)
        desc = _first_line(body) or act.title
        index.append(_INDEX_LINE.format(type=note_type.value, title=act.title, desc=desc))

    def _update(self, act: UpdateAction, index: list[str]) -> None:
        fname = act.filename
        path = self._dir / fname
        if not path.is_file():
            logger.warning("更新不存在的笔记: %s", fname)
            return
        fm = _read_frontmatter(path)
        old_title = str(fm.get("title") or "")
        old_type = str(fm.get("type") or "")
        created = str(fm.get("created") or datetime.now().astimezone().isoformat())
        now = datetime.now().astimezone().isoformat()
        body = act.content if act.content is not None else _body(path)
        new_title = act.title or old_title
        # 保留 created，更新 updated（F40）
        self._write_note(fname, old_type or act.type, new_title, created, now, body)
        desc = _first_line(body) or new_title
        self._upsert_index(index, old_type or act.type, old_title, new_title, desc)

    def _delete(self, act: UpdateAction, index: list[str]) -> None:
        fname = act.filename
        path = self._dir / fname
        title = ""
        if path.is_file():
            title = str(_read_frontmatter(path).get("title") or "")
            path.unlink()
        self._remove_index_by_title(index, title)

    def _upsert_index(
        self, index: list[str], type_: str, old_title: str, new_title: str, desc: str
    ) -> None:
        """按旧 title 匹配索引行替换；找不到则追加新行。"""
        for i, line in enumerate(index):
            if old_title and old_title in line:
                index[i] = _INDEX_LINE.format(type=type_, title=new_title, desc=desc)
                return
        index.append(_INDEX_LINE.format(type=type_, title=new_title, desc=desc))

    def _remove_index_by_title(self, index: list[str], title: str) -> None:
        """按 title 匹配移除索引行。"""
        for i, line in enumerate(index):
            if title and title in line:
                del index[i]
                return
