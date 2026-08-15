"""命令输入解析：判断是否斜杠命令、取命令名与尾随参数（ch10 T3，ch14 F29 修订）。

纯字符串操作，无副作用。Registry 上的 `lookup` 已能完成查找，分发处不必再封装。
"""

from __future__ import annotations


def parse(input_text: str) -> tuple[str, str, bool]:
    """解析输入文本为 (命令名小写, 尾随参数, 是否斜杠命令)。

    - 空白/空串/非 "/" 开头 → ("", "", False)（走正常对话）
    - 仅 "/" 或 "/ /help"（无命令名）→ ("", "", True)（lookup 必然 miss 走未命中分支）
    - "/help" → ("help", "", True)；"/HELP" → ("help", "", True)（大小写不敏感，F4）
    - "/help xx" → ("help", "xx", True)（ch14 F29 修订：带参数命令不再吞掉 tail，
      由注册的 args_handler 消费；`/help xx` 从 ch10 的 lookup miss 变为命中 help）
    - tail 已 strip（"/help  " → ("help", "", True)）
    """
    text = input_text.strip()
    if not text.startswith("/"):
        return "", "", False
    rest = text[1:]  # 保留原始形态，"/ /help" 首段为空白
    name, _, tail = rest.partition(" ")
    name = name.strip()
    if not name:
        return "", "", True
    return name.lower(), tail.strip(), True
