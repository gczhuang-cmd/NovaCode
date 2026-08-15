"""补充消息注入（F6）与规划模式提醒（F7）。

`system_reminder` 把补充指令用 `<system-reminder>` 标签包裹，构造请求时注入消息通道；
标签语义让模型理解这是系统补充上下文而非用户提问——不针对它直接回复。
此类消息每轮动态构造、不写入持久历史，故不污染缓存、不破坏角色交替。
"""

from __future__ import annotations

# 规划模式完整提醒（首轮与间隔轮注入）
PLAN_REMINDER_FULL = (
    "当前处于计划模式（PLAN MODE）。"
    "你只能使用只读工具（read_file / glob / grep）调研，产出清晰、分步的执行计划；"
    "不得写文件、改文件或执行 shell 命令。计划写完即停，等用户输入 /do 批准后再开始执行。"
)

# 规划模式精简提醒（其余轮次注入，一两句）
_PLAN_REMINDER_CONCISE = "仍处计划模式：只用只读工具调研，计划完成后等待 /do 批准执行。"


def system_reminder(body: str) -> str:
    """用 `<system-reminder>` 标签包裹补充指令。"""
    return f"<system-reminder>\n{body}\n</system-reminder>"


def plan_reminder(full: bool) -> str:
    """返回包好标签的规划模式提醒：full=True 完整版，否则精简版。"""
    return system_reminder(PLAN_REMINDER_FULL if full else _PLAN_REMINDER_CONCISE)


# /do 注入的用户消息：指示模型按上文已确认的计划开始执行。
EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"
