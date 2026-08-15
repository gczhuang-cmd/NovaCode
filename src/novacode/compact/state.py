"""会话级状态：替换决策账本、熔断器、文件追踪、会话目录。

Python asyncio 单线程事件循环保证串行，无需显式锁（N2 的并发安全由单线程模型保证）。
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _new_session_id() -> str:
    """生成 `YYYYMMDD-HHMMSS-<4hex>` 会话 id；随机源不可用时降级（ch09 F9）。

    时间戳取进程启动时刻的本地时间（F9 明确要求本地，非 UTC）。
    """
    try:
        hex_str = secrets.token_hex(2)  # 2 字节 = 4 hex 字符
    except Exception:  # noqa: BLE001 随机源不可用极罕见，降级到 time+random
        import random

        hex_str = random.Random(time.time()).randbytes(2).hex()
    # UTC 构造 + astimezone 转本地，得到本地时间戳（F9 要求本地，非 UTC）
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{hex_str}"


@dataclass
class SessionContext:
    """会话生命周期信息：session_id、会话目录与工具结果落盘目录（ch09 F10）。

    `session_dir` 放末位带默认值：兼容 ch08 的 `SessionContext(id, spill_dir)`
    位置构造；`new_session_context` / `open_session_context` 用关键字完整填充。
    """

    session_id: str
    spill_dir: str = ""  # session_dir + "/tool-results"
    session_dir: str = ""  # <workspace>/.novacode/sessions/<session_id>

    def __post_init__(self) -> None:
        if not self.session_dir:
            self.session_dir = str(Path(self.spill_dir).parent)
        if not self.spill_dir:
            self.spill_dir = str(Path(self.session_dir) / "tool-results")


def new_session_context(workspace: str) -> SessionContext:
    """构造会话上下文并自动创建落盘目录（已存在不报错，F10）。"""
    session_id = _new_session_id()
    session_dir = str(Path(workspace) / ".novacode" / "sessions" / session_id)
    spill_dir = str(Path(session_dir) / "tool-results")
    Path(spill_dir).mkdir(parents=True, exist_ok=True)
    return SessionContext(session_id=session_id, session_dir=session_dir, spill_dir=spill_dir)


def open_session_context(workspace: str, session_id: str) -> SessionContext | None:
    """按已存在的会话 ID 打开会话上下文（/resume 恢复场景，F22）。

    不创建目录：目录不存在返回 None，由调用方决定降级。
    """
    session_dir = str(Path(workspace) / ".novacode" / "sessions" / session_id)
    if not Path(session_dir).is_dir():
        return None
    return SessionContext(
        session_id=session_id,
        session_dir=session_dir,
        spill_dir=str(Path(session_dir) / "tool-results"),
    )


def parse_session_time(session_id: str) -> datetime | None:
    """从 ID 前 15 位解析 `YYYYMMDD-HHMMSS`（新格式，ch09）。

    返回附本地时区的 aware datetime 供比较；旧格式（如 `<unix_ts>-<8hex>`）
    解析失败返回 None（/resume 不展示、清理不误删，N3）。
    """
    try:
        # 本地时间戳解析（naive），随后 astimezone 附本地时区供比较
        naive = datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S")  # noqa: DTZ007
    except (ValueError, TypeError):
        return None
    return naive.astimezone()


class ContentReplacementState:
    """工具结果替换决策账本：已见集合 + id→预览映射。

    `decide_once` 是唯一写入口：查账本 → 决策 → 写账本在同一临界区原子完成。
    一旦写入 `_seen_ids`（无论 kept / replaced）本会话内不再翻转（F5d）；
    预览字符串写入后不允许修改。`result_for` 是只读查询（供第 1 层探测已决策项）。
    """

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    def result_for(self, tool_use_id: str, original: str) -> str | None:
        """只读查询账本：已决策返回存量结果（kept 原文 / replaced 预览），未决策返回 None。"""
        if tool_use_id in self._seen_ids:
            return self._replacements.get(tool_use_id, original)
        return None

    def decide_once(
        self,
        tool_use_id: str,
        original: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """持锁完成"查账本 → 决策 → 写账本"原子操作（F5）。

        decide 回调返回 (decision, preview)：
          - ("kept", _) → 写 _seen_ids，不写 _replacements；返回原 content。
          - ("replaced", preview) → 写 _seen_ids + _replacements；返回 preview。
          - ("skip", _) → 都不写；返回原 content（下一轮重试）。
        若 id 已 Seen：直接返回账本中存量结果，不再调 decide（不重新构造预览）。
        """
        if tool_use_id in self._seen_ids:
            return self._replacements.get(tool_use_id, original)
        decision, preview = decide()
        if decision == "replaced":
            self._seen_ids.add(tool_use_id)
            self._replacements[tool_use_id] = preview
            return preview
        if decision == "kept":
            self._seen_ids.add(tool_use_id)
        return original  # kept / skip 都返回原文；skip 不写账本


class CompactCircuitBreaker:
    """自动摘要连续失败熔断器。手动 / 紧急路径不读这个字段（F29）。"""

    def __init__(self) -> None:
        self._consecutive_failures = 0

    def record_success(self) -> None:
        """任何一次自动摘要成功，计数立即清零（F28）。"""
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """整轮 auto_compact（含 PTL 自重试）失败，计数 +1（F28）。"""
        self._consecutive_failures += 1

    def tripped(self) -> bool:
        """是否达到熔断阈值。"""
        from .const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES

        return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


@dataclass
class FileReadRecord:
    """一条最近成功读取的文件记录（恢复段数据源）。"""

    path: str
    content: str  # 不带行号前缀的纯净字节
    timestamp: datetime  # 最后一次成功读取的时间


class RecoveryState:
    """文件追踪状态：Agent 主循环写、compact 摘要时读。键为绝对路径（F19/F20）。"""

    def __init__(self) -> None:
        self._files: dict[str, FileReadRecord] = {}

    def record_file(self, path: str, content: str) -> None:
        """记录一次成功读取；非绝对路径先 resolve。"""
        abs_path = str(Path(path).resolve())
        self._files[abs_path] = FileReadRecord(
            path=abs_path, content=content, timestamp=datetime.now(UTC)
        )

    def snapshot(self) -> list[FileReadRecord]:
        """返回按 timestamp 倒序排序的拷贝列表（不暴露内部 dict）。"""
        return sorted(
            (FileReadRecord(r.path, r.content, r.timestamp) for r in self._files.values()),
            key=lambda r: r.timestamp,
            reverse=True,
        )
