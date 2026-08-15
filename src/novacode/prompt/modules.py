"""系统提示模块：按职责划分、按优先级拼装（F1）。

新增一类指令只需在模块列表里挂载新 `Module`，装配逻辑 `assemble_system` 不动。
稳定模块内容为常量，跨轮逐字节一致（N1）；环境/时间相关内容绝不进这里。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    """系统提示模块。

    name：模块标识（身份、系统约束 …），仅用于可读性与测试断言；
    priority：数值越小优先级越高、排越前；固定模块 10..70，可选空槽 80..100；
    content：模块正文；为空则装配时跳过（可选空槽）。
    """

    name: str
    priority: int
    content: str


def fixed_modules() -> list[Module]:
    """七个固定模块，按优先级：身份 → 系统约束 → 任务模式 → 动作执行 → 工具使用 → 语气风格 → 文本输出。"""
    return [
        Module(
            "身份",
            10,
            "你是一个在终端中运行的 AI 编程助手，名叫 NovaCode，是能使用工具的 Agent。",
        ),
        Module(
            "系统约束",
            20,
            "在工作目录约定内行事；不泄露密钥等敏感信息；对破坏性操作保持谨慎。",
        ),
        Module(
            "任务模式",
            30,
            "多步任务持续调用工具推进，读后再改，任务完成才给出最终简洁答复，不要每步停下来等待。",
        ),
        Module(
            "动作执行",
            40,
            "需要信息或操作时调用相应工具；连续只读工具可并发执行，有副作用工具谨慎串行执行。",
        ),
        Module(
            "工具使用",
            50,
            # F5 双重强化：关键约定在系统提示模块与工具描述中双重表述
            "优先用专用工具 read_file / glob / grep 而非用 bash 拼凑；编辑文件前必须先 read_file 读取确认。",
        ),
        Module("语气风格", 60, "用中文回答；简洁、直接、不奉承。"),
        Module(
            "文本输出",
            70,
            "涉及代码时给出可直接运行的完整示例；终答精炼，必要时用 Markdown（代码块/列表）。",
        ),
    ]


def optional_modules(
    instructions: str = "", memory: str = "", skills_catalog: str = ""
) -> list[Module]:
    """三个可选空槽：自定义指令 / 技能目录(skills-catalog) / 长期记忆。

    ch09：instructions 非空时填入「自定义指令」(priority 80)，memory 非空时
    填入「长期记忆」(priority 100)；空字符串跳过对应模块（content 为空装配时
    自动跳过、不留空行）。
    ch11：priority 90 槽位由「已激活 Skill」改为「技能目录」承载第一层 Skill
    名字+描述列表（active-skills 块改放 env context，不再进 system prompt）。
    """
    return [
        Module("自定义指令", 80, instructions),
        Module("技能目录", 90, skills_catalog),
        Module("长期记忆", 100, memory),
    ]


def assemble_system(mods: list[Module]) -> str:
    """按 priority 升序稳定排序，跳过空 content，以空行 `\\n\\n` 连接（F1/AC1）。"""
    ordered = sorted(mods, key=lambda m: m.priority)
    return "\n\n".join(m.content for m in ordered if m.content)


def build_system_prompt(instructions: str = "", memory: str = "", skills_catalog: str = "") -> str:
    """装配完整稳定系统提示（固定模块 + 可选槽；空槽自动跳过）。

    ch09：双参带默认值，`build_system_prompt()` 与 `build_system_prompt("", "")`
    逐字节一致（向后兼容，AC27）。
    ch11：第三参 `skills_catalog` 注入「技能目录」槽（priority 90，F21）。
    """
    return assemble_system(fixed_modules() + optional_modules(instructions, memory, skills_catalog))
