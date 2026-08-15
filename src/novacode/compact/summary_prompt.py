"""摘要 Prompt 模板与解析（F8/F9/F10，纯模板 + 字符串解析）。"""

from __future__ import annotations

import logging
import re

from ..llm import Message

logger = logging.getLogger(__name__)

SUMMARY_INSTRUCTION: str = """\
You are summarizing a coding agent conversation. Output in two phases.

<analysis>
（在这里写分析草稿，会被丢弃）
</analysis>

<summary>
## 1 主要请求和意图
## 2 关键技术概念
## 3 文件和代码段
## 4 错误和修复
## 5 问题解决过程
## 6 所有用户消息原文
## 7 待办任务
## 8 当前工作(最详细)
## 9 可能的下一步
</summary>

不要调用任何工具,输出纯文本。
"""


def serialize_conversation(msgs: list[Message]) -> str:
    """把对话扁平化成可读文本（不暴露 ToolCall.input 原 JSON 语义之外的细节）：
    - user/assistant 消息：`role: <content>`
    - assistant 工具调用：`[call <name> id=<id> args=<json string>]`
    - tool 消息内的每条 result：`[result id=<id> is_error=<bool>] <content>`
    纯函数，不依赖外部状态。
    """
    lines: list[str] = []
    for m in msgs:
        if m.tool_calls:
            for c in m.tool_calls:
                lines.append(f"[call {c.name} id={c.id} args={c.input}]")
            if m.content:
                lines.append(f"assistant: {m.content}")
            continue
        if m.tool_results:
            for r in m.tool_results:
                lines.append(f"[result id={r.tool_call_id} is_error={r.is_error}] {r.content}")
            continue
        lines.append(f"{m.role}: {m.content}")
    return "\n".join(lines)


def build_summary_prompt(msgs: list[Message]) -> list[Message]:
    """把对话嵌入固定模板，返回长度为 1 的列表：一条 user 消息。"""
    serialized = serialize_conversation(msgs)
    content = SUMMARY_INSTRUCTION + "\n\n[conversation]\n" + serialized
    return [Message(role="user", content=content)]


def extract_summary(raw: str) -> str:
    """从模型返回文本抠出 `<summary>...</summary>` 正文；找不到时返回原文 + 告警。"""
    matches = re.findall(r"<summary>(.*?)</summary>", raw, re.DOTALL)
    if not matches:
        logger.warning("summary tags not found")
        return raw
    return matches[-1].strip()
