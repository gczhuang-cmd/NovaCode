"""规则引擎：Rule / RuleSet、parse_rule、match_pattern（命令 glob 与文件路径 glob）。

规则以「工具名(模式)」声明，工具名用面向用户的友好名（Bash/Read/Write/Edit/Glob/Grep）。
ch12：模式段升级为结构化匹配——支持 `=精确`、`~正则`、`!反向`、无前缀=glob 四种前缀
（见 `permission.matcher.compile_matcher`）；不带模式段（`Bash`）表示匹配该工具的全部调用。
glob 语义不变：`*` 匹配任意字符序列；`**` 的跨目录段语义仅对文件路径有意义，
命令串不解释 `**`（等价于 `*`）。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from . import Decision
from .matcher import Matcher, compile_matcher

# 匹配 `Tool(pattern)` 或 `Tool`；工具名允许下划线（MCP 命名空间 `mcp__<server>__<tool>`，ch07）；
# 模式段贪婪取到最右括号——允许正则语法内的括号（如 `Bash(~^npm (install|test)$)`，ch12）。
# 括号不配对（有 `(` 无 `)`）整体不匹配 → parse_rule 返回失败（AC2 未闭合括号报错）。
_RULE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)(?:\((.*)\)|)$")


@dataclass
class Rule:
    """一条规则：友好工具名 + 匹配器（None=全匹配）+ allow/deny。"""

    tool: str = ""
    matcher: Matcher | None = None  # None 表示「该工具全匹配」
    allow: bool = False  # True=allow, False=deny
    raw: str = ""  # 原始模式段（含前缀，如 "=git status"），供错误日志与调试
    error: str = ""  # 解析失败原因（parse_rule 返回 ok=False 时非空）


@dataclass
class RuleSet:
    """一组规则，按 allow / deny 分列；`match` 先 deny 后 allow。"""

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        """在给定规则集内判出 allow/deny/未命中；deny 优先于 allow（单层内）。

        返回 (Allow|Deny, 是否命中)；未命中返回 (Allow, False)。
        """
        for r in self.deny:
            if r.tool == friendly and _rule_match(r, target):
                return Decision.DENY, True
        for r in self.allow:
            if r.tool == friendly and _rule_match(r, target):
                return Decision.ALLOW, True
        return Decision.ALLOW, False

    def match_deny(self, friendly: str, target: str) -> bool:
        """仅扫 deny 列：任一命中即 True（供「全局 deny 优先」跨层判定，AC5）。"""
        return any(r.tool == friendly and _rule_match(r, target) for r in self.deny)

    def match_allow(self, friendly: str, target: str) -> bool:
        """仅扫 allow 列：任一命中即 True（供「无 deny 时全局 allow」跨层判定，AC5）。"""
        return any(r.tool == friendly and _rule_match(r, target) for r in self.allow)


def _rule_match(r: Rule, target: str) -> bool:
    """单条规则命中判定：matcher 为 None（全匹配）恒 True，否则委托 matcher。"""
    if r.matcher is None:
        return True
    return r.matcher.match(target)


def parse_rule(s: str) -> tuple[Rule, bool]:
    """解析 `Tool(pattern)` 或 `Tool` 为 Rule；非法（空、括号不配对、匹配描述非法）返回 (Rule(), False)。

    pattern 为空 → matcher=None（该工具全匹配）；否则按前缀编译 matcher（Bash 走命令 glob）。
    失败时返回的 Rule 带 `raw`（原始模式段）与 `error`（原因），供调用方 stderr 提示（F4）。
    """
    text = (s or "").strip()
    m = _RULE_RE.match(text)
    if not m:
        return Rule(), False
    tool, pattern = m.group(1), m.group(2)
    if not tool:
        return Rule(), False
    if not pattern:
        return Rule(tool=tool, matcher=None), True
    try:
        matcher = compile_matcher(pattern, is_command=(tool == "Bash"))
    except ValueError as e:
        return Rule(tool=tool, raw=pattern, error=str(e)), False
    return Rule(tool=tool, matcher=matcher, raw=pattern), True


def match_pattern(pattern: str, target: str) -> bool:
    """glob 匹配：`pattern == ""` 恒匹配；命令串走「命令 glob」，文件路径按 `/` 分段。"""
    if not pattern:
        return True
    if "**" in pattern or "/" in target:
        # 含 `/` 的目标视为文件路径：按段匹配（`*` 段内、`**` 跨段）
        return _match_segments(pattern, target)
    # 纯命令串：`*` 匹配任意字符（含空格）、`**` 等价 `*`、其余字面
    rx = _glob_to_regex(pattern, file_path=False)
    return rx.fullmatch(target) is not None


def _match_segments(pattern: str, target: str) -> bool:
    """按 `/` 分段匹配：`**` 跨任意段数、`*` 段内任意字符。"""
    # 先做段级转换：每个段内 glob 转正则，`**` 段转 `.*`
    pat_segs = pattern.split("/")
    tgt_segs = target.split("/")
    return _segments_match(pat_segs, tgt_segs)


def _segments_match(pat: list[str], tgt: list[str]) -> bool:
    """递归段匹配：支持 `**` 跨段。"""
    if not pat:
        return not tgt
    if pat[0] == "**":
        # 一个 `**` 吃掉任意段数；先试 0 段，再试逐段
        for i in range(len(tgt) + 1):
            if _segments_match(pat[1:], tgt[i:]):
                return True
        return False
    if not tgt:
        return False
    if fnmatch.fnmatchcase(tgt[0], pat[0]):
        return _segments_match(pat[1:], tgt[1:])
    return False


def _glob_to_regex(pattern: str, *, file_path: bool) -> re.Pattern[str]:
    """把 glob 转成正则（`*`→任意字符、`?`→单字符；文件路径模式不走到这里）。"""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if file_path and i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                continue
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out))
