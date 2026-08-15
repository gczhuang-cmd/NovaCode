"""匹配器抽象：Matcher Protocol + 四种实现（Exact / Glob / Regex / Not）。

ch12：把权限规则的 glob 单一匹配（`permission.rule.match_pattern`）扩展为
结构化匹配类型。`compile_matcher` 解析「=exact / ~regex / !not / 无前缀=glob」，
供权限规则（`permission.rule.parse_rule`）与 Hook 条件表达式（`hook.matcher`）共用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class Matcher(Protocol):
    """规则匹配的统一接口；四种实现：ExactMatcher / GlobMatcher / RegexMatcher / NotMatcher。"""

    def match(self, s: str) -> bool: ...
    def __str__(self) -> str: ...  # 调试 / /hooks 输出用


@dataclass(frozen=True)
class ExactMatcher:
    """精确整串相等。"""

    value: str

    def match(self, s: str) -> bool:
        return s == self.value

    def __str__(self) -> str:
        return f"={self.value}"


@dataclass(frozen=True)
class GlobMatcher:
    """glob 匹配：复用 `match_pattern`（命令整串通配 / 文件路径按 `/` 分段，自判定）。

    `is_command` 保留作元数据（plan 语义区分）；实际判定交给 match_pattern。
    """

    pattern: str
    is_command: bool = False

    def match(self, s: str) -> bool:
        from .rule import match_pattern  # 函数内延迟导入：rule.py 会 import 本模块，模块级会成环

        return match_pattern(self.pattern, s)

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class RegexMatcher:
    """正则 search 匹配（命中即 True，非整串）。"""

    src: str
    compiled: re.Pattern[str]

    def match(self, s: str) -> bool:
        return self.compiled.search(s) is not None

    def __str__(self) -> str:
        return f"~{self.src}"


@dataclass(frozen=True)
class NotMatcher:
    """反向包装：对内层 matcher 取反。"""

    inner: Matcher

    def match(self, s: str) -> bool:
        return not self.inner.match(s)

    def __str__(self) -> str:
        return f"!{self.inner}"


def compile_matcher(pattern: str, *, is_command: bool) -> Matcher:
    """解析单条匹配描述串，返回 Matcher；失败抛 ValueError。

    描述串规则：
      "=value"  -> ExactMatcher（整串相等）
      "~regex"  -> RegexMatcher（search；re.compile 失败抛 ValueError）
      "!inner"  -> NotMatcher（对 compile_matcher(inner) 取反，支持 !=value / !~regex / !glob）
      "value"   -> GlobMatcher（沿用 match_pattern 的 glob 语义）
    """
    if not pattern:
        raise ValueError("empty matcher pattern")
    head, rest = pattern[0], pattern[1:]
    if head == "=":
        return ExactMatcher(rest)
    if head == "~":
        try:
            return RegexMatcher(rest, re.compile(rest))
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
    if head == "!":
        return NotMatcher(compile_matcher(rest, is_command=is_command))
    return GlobMatcher(pattern, is_command)
