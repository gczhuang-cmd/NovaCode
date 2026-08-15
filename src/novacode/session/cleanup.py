"""会话过期清理：删除超过 max_age 的新格式会话目录（ch09 F25/F26）。

同步函数；cli 启动时经 `asyncio.to_thread` 后台执行（R12）。只清理能解析出
新格式时间戳的目录，旧格式 session ID 不清理（N3 避免误删 ch08 遗留数据）。
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from ..compact.state import parse_session_time

logger = logging.getLogger(__name__)


def clean_expired(sessions_dir: str, max_age: timedelta) -> None:
    """删除 session ID 时间戳距当前超过 `max_age` 的会话目录（整个子目录）。"""
    root = Path(sessions_dir)
    if not root.is_dir():
        return
    now = datetime.now().astimezone()
    for d in root.iterdir():
        if not d.is_dir():
            continue
        created = parse_session_time(d.name)
        if created is None:
            continue  # 旧格式 ID 不清理（N3）
        if now - created > max_age:
            try:
                shutil.rmtree(d, ignore_errors=False)
                logger.info("清理过期会话: %s", d.name)
            except OSError as e:
                # 单个删除失败跳过，不影响其他目录（F26）
                logger.warning("清理会话失败 %s: %s", d.name, e)
