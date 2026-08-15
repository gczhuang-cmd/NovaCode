"""压缩后恢复三段：最近读过的文件、当前可用工具、边界提示（F15~F18，纯函数）。"""

from __future__ import annotations

from ..llm import ToolDefinition
from .const import ESTIMATE_CHARS_PER_TOKEN, RECOVERY_FILE_LIMIT, RECOVERY_TOKENS_PER_FILE
from .state import FileReadRecord

BOUNDARY_NOTICE: str = """\
需要文件原文、错误原文、用户原话时,请使用文件读取工具重新读取对应路径,不要依据摘要内容做猜测。
"""


def render_file_block(rec: FileReadRecord) -> str:
    """渲染单个文件快照：路径 / 时间戳 / 内容片段（超限保留头部、截尾部，F16）。"""
    char_limit = int(RECOVERY_TOKENS_PER_FILE * ESTIMATE_CHARS_PER_TOKEN)
    fragment = rec.content
    if len(fragment) > char_limit:
        fragment = fragment[:char_limit] + "\n(content truncated)"
    return f"### {rec.path}\n[read at] {rec.timestamp.isoformat()}\n{fragment}\n"


def render_tools_block(defs: list[ToolDefinition]) -> str:
    """渲染工具列表：每行一个工具名 + 用途 + 参数 schema 摘要（F17）。"""
    lines = [f"- {d.name}: {d.description}" for d in defs]
    return "\n".join(lines) + "\n"


def build_recovery_attachment(
    snapshot: list[FileReadRecord],
    tool_defs: list[ToolDefinition],
) -> str:
    """构造"恢复三段"内容（F15）。

    - `snapshot` 必须由调用方在 run_summary 入口一次性拍好（避免渲染期间漂移）；
    - 文件取前 RECOVERY_FILE_LIMIT 个（snapshot 已按时间戳倒序）；
    - 工具列表直接来自入参 tool_defs（与 stream 调用同一列表引用，保证一致性）。
    """
    parts: list[str] = []
    # 1. 最近读过的文件
    parts.append("## 最近读过的文件")
    files = snapshot[:RECOVERY_FILE_LIMIT]
    if files:
        for rec in files:
            parts.append(render_file_block(rec))
    else:
        parts.append("(无)")
    # 2. 当前可用工具
    parts.append("## 当前可用工具")
    parts.append(render_tools_block(tool_defs).rstrip("\n"))
    # 3. 边界提示
    parts.append("## 边界提示")
    parts.append(BOUNDARY_NOTICE.rstrip("\n"))
    return "\n\n".join(parts)
