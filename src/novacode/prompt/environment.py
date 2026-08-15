"""环境信息采集与渲染（F2/N4/N5）。

环境信息是系统提示的「独立第二段」，物理上与可缓存的稳定模块分属不同内容块，自身不进缓存。
采集快速且有界：git 状态用带 2s 超时的子进程调用，失败/非 git 目录降级为空。
不读取任何环境变量（N5 密钥安全）。
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from dataclasses import dataclass

# git 状态采集超时（秒），有界避免拖慢请求发起（N4）
_GIT_TIMEOUT = 2.0


@dataclass
class Environment:
    """运行环境信息。"""

    working_dir: str  # os.getcwd()
    platform: str  # sys.platform
    date: str  # 当天日期 isoformat
    git_status: str  # `git status --porcelain` 摘要；非 git 目录/取不到则留空
    version: str  # 应用版本（agent 透传）
    model: str  # provider.model

    def render(self) -> str:
        """渲染为「环境信息」段：逐行 `Key: Value`，空值项省略。"""
        lines = [
            ("工作目录", self.working_dir),
            ("平台", self.platform),
            ("日期", self.date),
            ("git", self.git_status),
            ("版本", self.version),
            ("模型", self.model),
        ]
        return "\n".join(f"{k}: {v}" for k, v in lines if v)


def _git_status() -> str:
    """采集 git 状态摘要；非 git 目录 / git 不可用 / 超时 → 返回空串。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,  # 非零退出（非 git 目录）是我们预期的降级路径
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""  # 非 git 目录（git status 返回非零）
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return "干净（无改动）"
    return f"{len(lines)} 个文件改动"


def gather_environment(version: str, model: str) -> Environment:
    """采集运行环境信息；任一项取不到时降级留空（N4）。"""
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    try:
        platform = sys.platform
    except Exception:  # noqa: BLE001 极端情形降级
        platform = ""
    try:
        date = datetime.date.today().isoformat()  # noqa: DTZ011 环境信息用本地日期（非 UTC），避免跨日混淆
    except Exception:  # noqa: BLE001 极端情形降级
        date = ""
    return Environment(
        working_dir=cwd,
        platform=platform,
        date=date,
        git_status=_git_status(),
        version=version,
        model=model,
    )
