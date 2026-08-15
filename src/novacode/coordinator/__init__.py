"""Coordinator Mode：双锁开关、工具白名单、系统提示词（ch15 T24，spec F52-F55）。

`is_enabled(cfg)` = `cfg.features.coordinator_mode and env_truthy(novaCODE_COORDINATOR_MODE)`。
N8：一旦启用 Lead 不可在运行时解锁，取消的唯一方式是退出重启。
"""

from __future__ import annotations

import os

COORDINATOR_ALLOWED_TOOLS: list[str] = [
    "Agent",
    "TeamCreate",
    "TeamDelete",
    "TeamTaskCreate",
    "TeamTaskGet",
    "TeamTaskList",
    "TeamTaskUpdate",
    "TeamSendMessage",
    "read_file",
    "glob",
    "grep",
    "bash",
]

SYSTEM_PROMPT_SUFFIX = (
    "\n\n# Coordinator 模式\n"
    "你是团队 Lead，负责把任务分解后派给队员执行，而不是自己动手。\n"
    "工作分四阶段：Research（调研定位）→ Synthesis（综合队员产出）→ "
    "Implementation（实现）→ Verification（收敛验证）。\n"
    "纪律：\n"
    "- 派出 Agent 或 TeamSendMessage 后，禁止立刻调 read_file / glob / grep / bash 自己探索；"
    "禁止用 sleep 或 TeamTaskList 轮询凑时间。task.Manager 完成时会推送 task-notification，"
    "下一轮被唤醒后再继续。\n"
    "- 派完队员后唯一该做的事：发一行总结「已派 N 名队员探索 X，等结果」，让本轮结束。\n"
    "- 允许自己用读类工具的场景仅限：Research 第一次目标定位；Synthesis 阶段读队员产出的报告文件；"
    "Verification 阶段 git diff / git status 等收敛操作。\n"
)


def env_truthy(v: str) -> bool:
    """`"1"` / `"true"` / `"yes"` / `"on"`（大小写不敏感）为真。"""
    return v.strip().lower() in {"1", "true", "yes", "on"}


def is_enabled(cfg) -> bool:
    """双锁全开才生效（spec F52）。"""
    features = getattr(cfg, "features", None)
    if features is None or not getattr(features, "coordinator_mode", False):
        return False
    return env_truthy(os.environ.get("novaCODE_COORDINATOR_MODE", ""))


def allowed_tools() -> list[str]:
    return list(COORDINATOR_ALLOWED_TOOLS)


def system_prompt_suffix() -> str:
    return SYSTEM_PROMPT_SUFFIX
