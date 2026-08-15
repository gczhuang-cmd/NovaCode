"""Hook 条件求值：把 permission.Matcher 应用到 payload 字段路径（F13/F15）。"""

from __future__ import annotations

import json

from .rule import CombineMode, Condition, Payload


def get_by_path(p: Payload, path: str) -> str:
    """按 `.` 分隔的字段路径取值并转字符串；路径不存在 / 遇 None 或非 dict 返回空串（F13）。

    标量（bool/int/float）用 str()（True → "True"，与 N6 JSON 输出一致）；
    嵌套对象转 json.dumps(sort_keys=True)（与动作输入序列化一致的稳定顺序）。
    """
    cur: object = p
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return ""
        cur = cur[part]
    return _stringify(cur)


def _stringify(v: object) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, sort_keys=True)


def eval_condition(c: Condition | None, p: Payload) -> bool:
    """求值条件表达式；c 为 None（无条件）恒 True（F11/F15）。

    ALL_OF 要求全部原子命中，ANY_OF 要求至少一个命中。
    """
    if c is None:
        return True
    hits = [atom.matcher.match(get_by_path(p, atom.field)) for atom in c.atoms]
    if c.mode is CombineMode.ALL_OF:
        return all(hits)
    return any(hits)
