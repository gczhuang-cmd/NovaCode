"""后端检测（ch15 T10，spec F14）：一次性决定，不做运行时回退。"""

from __future__ import annotations

import os
import shutil

from ..types import BackendType


def detect() -> BackendType:
    """按优先级：$TMUX → iTerm.app + it2 → PATH 有 tmux → in-process。"""
    if os.environ.get("TMUX"):
        return BackendType.TMUX
    if os.environ.get("TERM_PROGRAM") == "iTerm.app" and shutil.which("it2"):
        return BackendType.ITERM2
    if shutil.which("tmux"):
        return BackendType.TMUX
    return BackendType.IN_PROCESS
