"""Hook 配置加载：扫描两层 YAML、解析 hooks 数组、字段校验、Matcher 编译（ch12 F6-F8/N1）。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from ..permission.matcher import ExactMatcher, GlobMatcher, Matcher, NotMatcher, RegexMatcher
from .engine import Engine
from .event import is_blocking, parse_event
from .rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)

# 时长串解析：`30s` / `5m` / `1h` / 纯数字（F8/N1）
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)([smh]?)$")
_UNIT_SECONDS = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}

# 原子条件 match.type 允许值（F14）
_MATCH_TYPES = {"exact", "glob", "regex", "not"}

# 哨兵：条件加载错误（调用方据此跳过该 hook）；None 表示无条件
_MISSING = object()


def load(project_root: str | Path) -> Engine:
    """扫描两层 hooks.yaml，构造 Engine；加载错误一律 stderr 输出后跳过（N1/N9）。

    扫描顺序：项目级 → 用户级（F6）；跨文件同名冲突跳过后者（F7）。
    文件不存在不报错；存在但解析失败 / 顶层结构非法 → stderr 后跳过整文件（N9）。
    """
    paths = [
        Path(project_root) / ".novacode" / "hooks.yaml",
        Path.home() / ".novacode" / "hooks.yaml",
    ]
    rules: list[Rule] = []
    sources: list[str] = []
    seen_names: set[str] = set()
    for p in paths:
        if not p.exists():
            continue
        file_rules = _load_file(p, seen_names)
        if file_rules is None:
            # 整文件失败（YAML 解析错 / 顶层结构非法）：跳过整文件且不计入来源（N9）
            continue
        rules.extend(file_rules)
        sources.append(str(p))
    return Engine(rules=rules, sources=sources)


def _load_file(path: Path, seen_names: set[str]) -> list[Rule] | None:
    """加载单个 hooks.yaml；返回成功编译的规则列表（失败规则已打 stderr）。

    None 表示整文件加载失败（解析错 / 顶层非法），调用方跳过该文件。
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        print(f"[hook] {path}: yaml parse failed: {e}", file=sys.stderr)
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("hooks"), list):
        print(f"[hook] {path}: top-level 'hooks' list required, skipped", file=sys.stderr)
        return None
    rules: list[Rule] = []
    for idx, item in enumerate(raw["hooks"]):
        if not isinstance(item, dict):
            print(f"[hook] {path}: hooks[{idx}] not a mapping, skipped", file=sys.stderr)
            continue
        rule = _compile_rule(str(path), idx, item, seen_names)
        if rule is not None:
            rules.append(rule)
    return rules


def _compile_rule(source: str, idx: int, raw: dict, seen_names: set[str]) -> Rule | None:
    """字段校验 + Matcher 编译；失败打 stderr 并返回 None（跳过该条，N1）。"""
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        print(f"[hook] {source}: hooks[{idx}] missing or empty 'name', skipped", file=sys.stderr)
        return None
    # 跨文件同名冲突：跳过后者（F7）
    if name in seen_names:
        print(f'[hook] {source}: hook "{name}" already defined, skipped', file=sys.stderr)
        return None
    # event 枚举
    event_raw = raw.get("event")
    event = parse_event(event_raw) if isinstance(event_raw, str) else None
    if event is None:
        print(
            f'[hook] {source}: hook "{name}": unknown event "{event_raw}", skipped',
            file=sys.stderr,
        )
        return None
    # action
    action = _compile_action(source, name, raw.get("action"))
    if action is None:
        return None
    # if 条件（None=无条件；_MISSING=加载错误）
    condition = _compile_condition(source, name, raw.get("if"))
    if condition is _MISSING:
        return None
    cond: Condition | None = condition if isinstance(condition, Condition) else None
    # async + 拦截事件冲突（F28）
    is_async = bool(raw.get("async", False))
    if is_async and is_blocking(event):
        print(
            f'[hook] {source}: hook "{name}": async not allowed for blocking events, skipped',
            file=sys.stderr,
        )
        return None
    # timeout 解析（F8/F18/F24）
    timeout_s = 30.0
    timeout_raw = raw.get("timeout")
    if timeout_raw is not None:
        parsed = _parse_duration(timeout_raw)
        if parsed is None:
            print(
                f'[hook] {source}: hook "{name}": invalid timeout "{timeout_raw}", skipped',
                file=sys.stderr,
            )
            return None
        timeout_s = parsed
    rule = Rule(
        name=name,
        event=event,
        action=action,
        condition=cond,
        only_once=bool(raw.get("only_once", False)),
        asyncio_mode=is_async,
        timeout_s=timeout_s,
        source=source,
    )
    seen_names.add(name)
    return rule


def _compile_action(source: str, name: str, action_raw: object) -> Action | None:
    """解析 action 对象与各类型子字段；失败返回 None。"""
    if not isinstance(action_raw, dict):
        print(
            f'[hook] {source}: hook "{name}": "action" must be a mapping, skipped', file=sys.stderr
        )
        return None
    atype_raw = action_raw.get("type")
    try:
        atype = ActionType(atype_raw)
    except (ValueError, TypeError):
        print(
            f'[hook] {source}: hook "{name}": invalid action type "{atype_raw}", skipped',
            file=sys.stderr,
        )
        return None
    if atype is ActionType.SHELL:
        cmd = action_raw.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            print(
                f'[hook] {source}: hook "{name}": shell action missing "command", skipped',
                file=sys.stderr,
            )
            return None
        return Action(type=atype, shell=ShellAction(command=cmd))
    if atype is ActionType.PROMPT:
        text = action_raw.get("text")
        if not isinstance(text, str):
            print(
                f'[hook] {source}: hook "{name}": prompt action missing "text", skipped',
                file=sys.stderr,
            )
            return None
        return Action(type=atype, prompt=PromptAction(text=text))
    if atype is ActionType.HTTP:
        url = action_raw.get("url")
        if not isinstance(url, str) or not url.strip():
            print(
                f'[hook] {source}: hook "{name}": http action missing "url", skipped',
                file=sys.stderr,
            )
            return None
        headers = action_raw.get("headers")
        if headers is not None and not isinstance(headers, dict):
            print(
                f'[hook] {source}: hook "{name}": http "headers" must be a mapping, skipped',
                file=sys.stderr,
            )
            return None
        body = action_raw.get("body")
        if body is not None and not isinstance(body, str):
            print(
                f'[hook] {source}: hook "{name}": http "body" must be a string, skipped',
                file=sys.stderr,
            )
            return None
        return Action(
            type=atype,
            http=HttpAction(
                url=url,
                method=str(action_raw.get("method", "POST")),
                headers=dict(headers) if isinstance(headers, dict) else {},
                body=body,
            ),
        )
    if atype is ActionType.SUBAGENT:
        agent_name = action_raw.get("agent_name")
        prompt = action_raw.get("prompt")
        if not isinstance(agent_name, str) or not isinstance(prompt, str):
            print(
                f'[hook] {source}: hook "{name}": subagent action needs "agent_name" and "prompt", skipped',
                file=sys.stderr,
            )
            return None
        return Action(type=atype, subagent=SubagentAction(agent_name=agent_name, prompt=prompt))
    return None


def _compile_condition(source: str, name: str, if_raw: object) -> Condition | None | object:
    """解析 if 表达式：None=无条件；_MISSING=加载错误（调用方跳过该 hook）；否则返回 Condition。"""
    if if_raw is None:
        return None
    if not isinstance(if_raw, dict):
        print(f'[hook] {source}: hook "{name}": "if" must be a mapping, skipped', file=sys.stderr)
        return _MISSING
    keys = [k for k in ("all_of", "any_of") if k in if_raw]
    if len(keys) != 1:
        print(
            f'[hook] {source}: hook "{name}": "if" must contain exactly one of all_of/any_of, skipped',
            file=sys.stderr,
        )
        return _MISSING
    mode = CombineMode.ALL_OF if keys[0] == "all_of" else CombineMode.ANY_OF
    atoms_raw = if_raw[keys[0]]
    if not isinstance(atoms_raw, list):
        print(
            f'[hook] {source}: hook "{name}": "{keys[0]}" must be a list, skipped',
            file=sys.stderr,
        )
        return _MISSING
    atoms: list[AtomCondition] = []
    for atom_raw in atoms_raw:
        atom = _compile_atom(source, name, atom_raw)
        if atom is None:
            return _MISSING
        atoms.append(atom)
    return Condition(mode=mode, atoms=atoms)


def _compile_atom(source: str, name: str, atom_raw: object) -> AtomCondition | None:
    """解析单个原子条件（field + match）；失败返回 None。"""
    if not isinstance(atom_raw, dict):
        print(
            f'[hook] {source}: hook "{name}": atom condition must be a mapping, skipped',
            file=sys.stderr,
        )
        return None
    field = atom_raw.get("field")
    if not isinstance(field, str) or not field.strip():
        print(
            f'[hook] {source}: hook "{name}": atom condition missing "field", skipped',
            file=sys.stderr,
        )
        return None
    matcher = _compile_match(source, name, atom_raw.get("match"))
    if matcher is None:
        return None
    return AtomCondition(field=field, matcher=matcher)


def _compile_match(source: str, name: str, match_raw: object) -> Matcher | None:
    """编译 match 描述（F14）：{type: exact|glob|regex, value} 或 {type: not, inner: {...}}。

    正则编译失败、not 缺 inner、inner 非法均视为加载错误（调用方跳过该 hook）。
    """
    if not isinstance(match_raw, dict):
        print(
            f'[hook] {source}: hook "{name}": atom "match" must be a mapping, skipped',
            file=sys.stderr,
        )
        return None
    mtype = match_raw.get("type")
    if mtype not in _MATCH_TYPES:
        print(
            f'[hook] {source}: hook "{name}": invalid match type "{mtype}", skipped',
            file=sys.stderr,
        )
        return None
    if mtype == "exact":
        v = match_raw.get("value")
        if not isinstance(v, str):
            print(
                f'[hook] {source}: hook "{name}": exact match needs "value", skipped',
                file=sys.stderr,
            )
            return None
        return ExactMatcher(v)
    if mtype == "glob":
        v = match_raw.get("value")
        if not isinstance(v, str):
            print(
                f'[hook] {source}: hook "{name}": glob match needs "value", skipped',
                file=sys.stderr,
            )
            return None
        return GlobMatcher(v, is_command=False)  # hook 上下文统一路径语义（T8 决策修正）
    if mtype == "regex":
        v = match_raw.get("value")
        if not isinstance(v, str):
            print(
                f'[hook] {source}: hook "{name}": regex match needs "value", skipped',
                file=sys.stderr,
            )
            return None
        try:
            return RegexMatcher(v, re.compile(v))
        except re.error as e:
            print(
                f'[hook] {source}: hook "{name}": invalid regex "{v}": {e}, skipped',
                file=sys.stderr,
            )
            return None
    if mtype == "not":
        inner = _compile_match(source, name, match_raw.get("inner"))
        if inner is None:
            return None
        return NotMatcher(inner)
    return None


def _parse_duration(s: object) -> float | None:
    """解析时长：`30` / `30s` / `5m` / `1h` / 浮点秒；失败返回 None。"""
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str):
        return None
    m = _DURATION_RE.match(s.strip())
    if not m:
        return None
    return float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
