"""跨进程文件锁（ch15 T5，spec G8/F33）。

mailbox 与 tasks 共用。`O_CREAT|O_EXCL` 抢占（EEXIST 视为已锁）；失败按 5-100ms
随机抖动重试 10 次；持锁超过 10 秒视为 stale（`st_mtime` 判定）直接清掉重试；
context manager 退出时 unlink。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path

LOCK_MAX_RETRIES = 10
LOCK_STALE_AFTER = 10.0
LOCK_BACKOFF_MIN = 0.005
LOCK_BACKOFF_MAX = 0.1


@asynccontextmanager
async def acquire(lock_path: str):
    """抢锁；进入后持锁，退出时释放。抢不到抛 TimeoutError（不丢数据，调用方兜底）。"""
    p = Path(lock_path)
    acquired = False
    for attempt in range(LOCK_MAX_RETRIES):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            # stale 检测：超过 10 秒的锁清掉后立即重试一次（下一轮循环）
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if time.time() - mtime > LOCK_STALE_AFTER:
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            if attempt == LOCK_MAX_RETRIES - 1:
                raise TimeoutError(f"获取文件锁超时: {lock_path}")
            await asyncio.sleep(random.uniform(LOCK_BACKOFF_MIN, LOCK_BACKOFF_MAX))
    if not acquired:
        raise TimeoutError(f"获取文件锁超时: {lock_path}")
    try:
        yield
    finally:
        try:
            p.unlink()
        except OSError:
            pass
