"""Fork 路径辅助函数（ch13 T12，spec F22~F25）。

`build_forked_messages` 克隆父对话、补全悬空 tool_use、注入 Fork Boilerplate；
任务文本由 `run_to_completion` 的 `if task: conv.add_user(task)` 统一追加（不重复）。
`is_fork_context` 本期不作运行时拦截闸（嵌套阻断统一走 ALL_AGENT_DISALLOWED_TOOLS
工具过滤，见 spec 修订记录），保留为辅助/测试函数。
"""

from __future__ import annotations

import copy

from ..llm import ROLE_ASSISTANT, ROLE_TOOL, Message, ToolResult

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

# Fork 子 Agent 首条 user 消息的内容，约束其行为。
FORK_BOILERPLATE = """<fork_boilerplate>
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则(不可协商):
1. 不能再 Fork(调用 Agent 工具会被拦截)。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具:读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告以 "Scope:" 开头,500 字以内。
</fork_boilerplate>
"""


def build_forked_messages(parent_msgs: list[Message]) -> list[Message]:
    """把父对话克隆到 Fork 子对话：深拷贝 + 补悬空 tool_use + 追加 Boilerplate user。

    返回新消息列表，直接用 `Conversation.from_messages` 装载即可。
    """
    cloned = copy.deepcopy(parent_msgs)
    # 补全「最后一条 assistant」里未配对的 tool_calls（其后无 ROLE_TOOL 回合消费的 ID）
    last_assistant_idx = None
    for i in range(len(cloned) - 1, -1, -1):
        if cloned[i].role == ROLE_ASSISTANT:
            last_assistant_idx = i
            break
    if last_assistant_idx is not None:
        last = cloned[last_assistant_idx]
        if last.tool_calls:
            ids = {tc.id for tc in last.tool_calls}
            consumed: set[str] = set()
            for m in cloned[last_assistant_idx + 1 :]:
                if m.role == ROLE_TOOL:
                    consumed.update(tr.tool_call_id for tr in m.tool_results)
            orphan = [tid for tid in ids if tid not in consumed]
            if orphan:
                cloned.append(
                    Message(
                        role="tool",  # 字面量（ROLE_* 常量是 str，mypy 不匹配 Literal）
                        tool_results=[
                            ToolResult(tool_call_id=tid, content="[forked, skipped]", is_error=True)
                            for tid in orphan
                        ],
                    )
                )
    # 追加 Boilerplate user（首条约束消息；任务文本由 run_to_completion add_user 注入）
    cloned.append(Message(role="user", content=FORK_BOILERPLATE))
    return cloned


def is_fork_context(msgs: list[Message]) -> bool:
    """判定消息历史是否来自 Fork（扫描 `<fork_boilerplate>` 标签）。"""
    return any((m.role == "user" and FORK_BOILERPLATE_TAG in (m.content or "")) for m in msgs)
