"""Slug 校验与扁平化（ch14 T1，spec F1/G2/G3）。"""

from __future__ import annotations

import re

# 单段字符集（spec F1）：大小写字母/数字/点/下划线/连字符
_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
# 总长度上限（spec F1）
MAX_SLUG_LENGTH = 64


def validate_slug(name: str) -> None:
    """校验 worktree slug；非法抛 ValueError 携带具体原因（spec F1）。

    规则：非空、总长 ≤ 64、按 `/` 切段后每段匹配 `^[a-zA-Z0-9._-]+$` 且不能是
    `.` / `..`、无连续 `//`、无首末 `/`。防 LLM 输入触发路径遍历。
    """
    if not name:
        raise ValueError("slug 不能为空")
    if len(name) > MAX_SLUG_LENGTH:
        raise ValueError(f"slug 长度超限（{len(name)} > {MAX_SLUG_LENGTH}）")
    if name.startswith("/") or name.endswith("/"):
        raise ValueError("slug 不能以 / 开头或结尾")
    if "//" in name:
        raise ValueError("slug 不允许连续 //")
    for seg in name.split("/"):
        if seg in (".", ".."):
            raise ValueError(f"slug 段 {seg!r} 不允许（路径遍历）")
        if not _SEGMENT_RE.fullmatch(seg):
            raise ValueError(f"slug 段 {seg!r} 含非法字符（仅允许 [a-zA-Z0-9._-]）")


def flat_slug(name: str) -> str:
    """嵌套 slug 扁平化：`/` 替换为 `+`（spec G3，避免 Git 分支 D/F 冲突）。"""
    return name.replace("/", "+")
