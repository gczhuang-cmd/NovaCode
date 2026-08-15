"""Skill body 渲染：$ARGUMENTS 替换 + allowed_tools 顶部提示（ch11 T8）。"""

from __future__ import annotations

from .types import Skill


def render_body(s: Skill, args: str) -> str:
    """渲染最终注入文本（F9/F27）。

    - allowed_tools 非空时在 body 顶部插一段"建议工具"提示；
    - 替换所有 `$ARGUMENTS`；
    - 无占位符且 args 非空时在末尾追加 `## User Request`。
    """
    body = s.prompt_body
    if s.meta.allowed_tools:
        tools = ", ".join(s.meta.allowed_tools)
        hint = (
            f"This skill is designed to use only these tools: {tools}. "
            "Prefer them over other tools when possible.\n\n---\n\n"
        )
        body = hint + body
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", args)
    elif args.strip():
        body += f"\n\n## User Request\n\n{args}"
    return body
