"""三层 novaCODE.md 加载 + @include 展开（ch09 第 1 层，F1~F8）。

Loader 按优先级加载三层指令文件（项目根 → .novacode/ → 用户级），展开
`@include` 引用并做深度 / 环路 / 逃逸 / 二进制检测，返回拼接后的纯文本。
本模块不做系统提示注入（注入在 cli / Agent 侧）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# @include 独占行：`@include ` 后跟路径（F2，独占一行才替换）
_INCLUDE_RE = re.compile(r"^@include\s+(.+)$")

# 深度 / 环路 / 逃逸 / 二进制 警告注释（F3/F4/F5/F6）
_WARN_DEPTH = "<!-- @include 超过最大嵌套深度，已跳过: {path} -->"
_WARN_CYCLE = "<!-- @include 检测到环路，已跳过: {path} -->"
_WARN_BOUNDARY = "<!-- @include 路径超出允许范围，已跳过: {path} -->"
_WARN_BINARY = "<!-- @include 二进制文件，已跳过: {path} -->"


def _is_within(path: str, boundary: str) -> bool:
    """`path` 的绝对路径是否位于 `boundary` 之内（含边界本身，F5）。"""
    try:
        return os.path.commonpath(
            [os.path.realpath(path), os.path.realpath(boundary)]
        ) == os.path.realpath(boundary)
    except ValueError:
        return False  # 不同盘符等 → commonpath 抛 ValueError，视为越界


@dataclass
class Loader:
    """按优先级加载三层指令文件并展开 @include（ch09 F1）。"""

    project_root: str
    user_home: str
    max_depth: int = 5

    def load(self) -> str:
        """按优先级扫描三个路径，返回拼接后的完整指令文本。

        ① `<project_root>/novaCODE.md`（最高）→ ② `<project_root>/.novacode/novaCODE.md`
        → ③ `<user_home>/.novacode/novaCODE.md`（最低）。高优先级在前，各层以空行
        `\\n\\n` 分隔；缺失层静默跳过，全部为空返回空字符串（F1）。
        """
        root = os.path.realpath(self.project_root)
        user = os.path.realpath(self.user_home)
        levels = [
            # (指令文件路径, 根边界)
            (os.path.join(root, "novaCODE.md"), root),
            (os.path.join(root, ".novacode", "novaCODE.md"), root),
            (
                os.path.join(user, ".novacode", "novaCODE.md"),
                os.path.join(user, ".novacode"),
            ),
        ]
        parts: list[str] = []
        for path, boundary in levels:
            text = self._load_file(path, boundary, depth=1, visited=set())
            if text.strip():
                parts.append(text)
        return "\n\n".join(parts)

    def _load_file(
        self,
        path: str,
        boundary: str,
        depth: int,
        visited: set[str],
    ) -> str:
        """加载单个文件，处理 @include 展开。

        boundary 是路径逃逸检测的根边界；depth 当前嵌套层数（顶层 novaCODE.md=1，
        被 include 的文件算下一层）；visited 是本条展开链已解析的绝对路径集合
        （环路检测，F4）。
        """
        abs_path = os.path.realpath(path)
        # F4 环路检测：同一条展开链上同一绝对路径只加载一次
        if abs_path in visited:
            return _WARN_CYCLE.format(path=path)
        # F5 路径逃逸：解析后的绝对路径必须仍在根边界之内
        if not _is_within(abs_path, os.path.realpath(boundary)):
            return _WARN_BOUNDARY.format(path=path)
        # F6 缺失文件静默跳过（非错误）；读失败降级为空（N5）
        if not os.path.exists(abs_path):
            return ""
        try:
            raw = Path(abs_path).read_bytes()
        except OSError:
            return ""
        # F6 二进制文件（前 512 字节含 \x00）视为不可读，跳过并追加警告
        if b"\x00" in raw[:512]:
            return _WARN_BINARY.format(path=path)
        text = raw.decode("utf-8", errors="replace")
        # 加入展开链后再扫描，使 include 回顶层能命中环路
        visited = visited | {abs_path}
        base = os.path.dirname(abs_path)
        out: list[str] = []
        for line in text.splitlines():
            m = _INCLUDE_RE.match(line)
            if m is None:
                out.append(line)  # 非独占 @include 行保持原文（F2）
                continue
            inc = m.group(1).strip()
            # F3 深度限制：被 include 文件是第 depth+1 层，超过 max_depth 时
            # 该 @include 行保留原文不展开，并追加一行警告注释
            if depth + 1 > self.max_depth:
                out.append(line)
                out.append(_WARN_DEPTH.format(path=inc))
                continue
            inc_abs = os.path.join(base, inc)  # 相对当前文件所在目录解析（F2）
            out.append(self._load_file(inc_abs, boundary, depth + 1, visited))
        return "\n".join(out)
