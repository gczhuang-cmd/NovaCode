"""引擎与前四层判定流水线：Engine、new_engine、check、mode_fallback、start_mode。

`check` 实现前四层（黑名单 → 沙箱 → 规则 → 模式兜底），逐层短路：
任一层给出 Allow/Deny 即返回，不再进入后续层；返回 `Ask` 作为「请走第五层
（人在回路）」的信号，由 agent 在编排层驱动。被跳过的层视为「未拦、继续下一层」
（非命令执行工具跳过黑名单、命令执行工具跳过沙箱）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..llm import ToolCall
from . import Category, Decision, Mode, parse_mode
from .blacklist import hits_blacklist
from .rule import RuleSet
from .sandbox import resolve_root, sandbox_ok
from .settings import (
    Settings,
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)

# 类别中文名（reason 文案用）
_CAT_NAMES: dict[Category, str] = {
    Category.READ: "只读",
    Category.WRITE: "文件写",
    Category.EXEC: "命令执行",
}


@dataclass
class Engine:
    """前四层判定状态：项目根、三级规则集、本地层写入路径、启动模式。

    `blacklist` 字段仅作占位兼容（黑名单是模块级内置，不可配）；判定走
    `blacklist.hits_blacklist`。
    """

    root: str  # 项目根（绝对、已解析符号链接）
    blacklist: list = field(default_factory=list)  # 占位：黑名单不可配（N1）
    user: RuleSet = field(default_factory=RuleSet)
    project: RuleSet = field(default_factory=RuleSet)
    local: RuleSet = field(default_factory=RuleSet)
    local_path: str = ""  # 永久放行（人在回路「永久」）的写入目标
    start_mode: Mode = Mode.DEFAULT  # 启动默认模式（取自配置）

    def check(self, mode: Mode, call: ToolCall, read_only: bool) -> tuple[Decision, str]:
        """前四层判定（实例方法；等价模块级 `check(engine, ...)`）。"""
        return _check_impl(self, mode, call, read_only)

    def persist_local_allow(self, call: ToolCall) -> None:
        """把「永久」的精确 allow 规则写入本地层文件并同步内存（方法形式）。"""
        from .persist import persist_local_allow as _impl

        _impl(self, call)


def new_engine(root: str) -> tuple[Engine, Exception | None]:
    """构造引擎：解析项目根、加载三层配置、确定启动模式。

    配置文件格式错误只降级该文件为空规则集，绝不致错；唯一可能返回非 None err 的
    情形是项目根不可解析——此时仍返回非 None 的「空规则安全引擎」+ err
    （cli 注入永不为 None、check 不抛）。
    """
    try:
        root_resolved = resolve_root(root)
    except Exception as e:  # noqa: BLE001 仅 resolve_root 致命错；其余情况全部降级
        return Engine(root=root, start_mode=Mode.DEFAULT), e
    home = Path.home()
    user_path = home / ".novacode" / "settings.yaml"
    project_path = Path(root_resolved) / ".novacode" / "settings.yaml"
    local_path = Path(root_resolved) / ".novacode" / "settings.local.yaml"
    user = _safe_rule_set(str(user_path))
    project = _safe_rule_set(str(project_path))
    local = _safe_rule_set(str(local_path))
    # 启动默认模式：本地 > 项目 > 用户，parse_mode 成功者生效，皆无则 default
    start = Mode.DEFAULT
    for path in (str(local_path), str(project_path), str(user_path)):
        s, _ = _safe_settings(path)
        m, ok = parse_mode(s.default_mode)
        if ok:
            start = m
            break
    return (
        Engine(
            root=root_resolved,
            user=user,
            project=project,
            local=local,
            local_path=str(local_path),
            start_mode=start,
        ),
        None,
    )


def _safe_settings(path: str) -> tuple[Settings, bool]:
    """加载配置；失败降级为空 Settings 并返回 False（不抛致命异常）。"""
    try:
        return load_settings(path), True
    except SettingsError:
        return Settings(), False


def _safe_rule_set(path: str) -> RuleSet:
    s, _ = _safe_settings(path)
    return to_rule_set(s)


def mode_fallback(mode: Mode, cat: Category) -> Decision:
    """F5 矩阵兜底（规则未命中时）：只产 Allow 或 Ask，绝不产 Deny。

    | 模式 | 只读 | 文件写 | 命令执行 |
    | default | Allow | Ask | Ask |
    | acceptEdits | Allow | Allow | Ask |
    | plan | Allow | Ask | Ask |
    | bypassPermissions | Allow | Allow | Allow |
    """
    if cat == Category.READ or mode == Mode.BYPASS:
        return Decision.ALLOW
    if mode == Mode.ACCEPT_EDITS and cat == Category.WRITE:
        return Decision.ALLOW
    return Decision.ASK


def _check_impl(
    engine: Engine, mode: Mode, call: ToolCall, read_only: bool
) -> tuple[Decision, str]:
    """前四层判定（agent 每次执行工具前调用）。返回 (裁决, 原因)。

    read_only 由调用方按批类型给定（只读批=True / 串行批=False，等价于
    registry.is_read_only(name)）。
    """
    cat = categorize(call.name, read_only)
    friendly = friendly_name(call.name)
    target, is_file, ok = extract_target(call)
    # ① 黑名单（仅 EXEC 类，且能取到命令串）
    if cat == Category.EXEC and target and hits_blacklist(target):
        return Decision.DENY, f"命中危险命令黑名单：{target[:60]}"
    # ② 沙箱（仅文件类工具）
    if is_file:
        if not ok:
            return Decision.DENY, "无法解析文件路径参数，安全拒绝"
        if not sandbox_ok(engine.root, target):
            return Decision.DENY, f"路径在项目目录之外：{target}"
    # ③ 规则引擎：三层合并为同一集合判定，deny 全局优先于 allow（层级不参与裁决，AC5）
    match_target = _match_target(engine.root, is_file, target)
    layers = (engine.local, engine.project, engine.user)
    if any(rs.match_deny(friendly, match_target) for rs in layers):
        return Decision.DENY, f"匹配 deny 规则：{friendly}({match_target})"
    if any(rs.match_allow(friendly, match_target) for rs in layers):
        return Decision.ALLOW, ""
    # ④ 模式兜底
    if mode_fallback(mode, cat) == Decision.ALLOW:
        return Decision.ALLOW, ""
    return Decision.ASK, f"{mode} 模式下 {_CAT_NAMES[cat]}类操作需确认"


def check(engine: Engine, mode: Mode, call: ToolCall, read_only: bool) -> tuple[Decision, str]:
    """模块级便捷入口：委托给 `Engine.check` 实例方法。"""
    return engine.check(mode, call, read_only)


def start_mode(engine: Engine) -> Mode:
    """启动默认模式。"""
    return engine.start_mode


def _match_target(root: str, is_file: bool, target: str) -> str:
    """规则匹配对象：文件类→项目相对路径（统一 `/` 分隔）；命令类→命令串原样。"""
    if not is_file:
        return target
    abs_p = (
        os.path.abspath(os.path.join(root, target))
        if not os.path.isabs(target)
        else os.path.abspath(target)
    )
    try:
        rel = os.path.relpath(abs_p, root)
    except ValueError:
        return target
    return rel.replace(os.sep, "/")
