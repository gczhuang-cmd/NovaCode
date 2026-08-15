"""MCP 客户端配置：两层 YAML 加载、`${VAR}` 展开、字段校验、按 server 名合并。

对外接口：
- `Config` / `ServerConfig`：归一化的 mcp_servers 配置。
- `load_config(root) -> Config`：加载用户级 `~/.novacode/config.yaml` 与项目级
  `<root>/.novacode.yaml`，合并、展开、校验；**永不抛出**（F1/F2/F3/N2）。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

import yaml

# 环境变量引用：${NAME}——仅对 env / headers 的值生效（F3），command/args/名字不展开
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class ServerConfig:
    """单个 MCP server 的完整定义（已展开 `${VAR}`、已校验）。"""

    type: Literal["stdio", "http"]
    command: str = ""  # stdio 必填
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""  # http 必填
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """mcp_servers 在内存中的归一化形式（已合并）。"""

    servers: dict[str, ServerConfig] = field(default_factory=dict)


@dataclass
class _RawServer:
    """配置文件的原始 server 定义（全部字段可选，缺省用默认）。"""

    type: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def load_config(root: str) -> Config:
    """加载用户级 + 项目级 mcp_servers 配置并合并（永不抛出）。

    - 用户级 `~/.novacode/config.yaml`（`Path.home()` 失败时跳过该层）；
    - 项目级 `<root>/.novacode.yaml`；
    - 文件缺失视为空层；格式非法 → stderr 告警 + 该层视为空（降级）；
    - 合并语义：按 server 名，项目级同名 server 完整覆盖用户级（F1，无字段级合并）。
    """
    try:
        home = Path.home()
    except Exception:  # noqa: BLE001 Path.home() 在异常环境可能失败，降级跳过用户层
        home = None
    user: dict[str, _RawServer] = {}
    if home is not None:
        user = _load_file(home / ".novacode" / "config.yaml")
        for name, srv in user.items():
            _apply_expansion(name, srv)
    project = _load_file(Path(root) / ".novacode.yaml")
    for name, srv in project.items():
        _apply_expansion(name, srv)
    merged = _merge_servers(user, project)
    cfg = Config()
    for name in sorted(merged):
        srv = _validate_server(name, merged[name])
        if srv is not None:
            cfg.servers[name] = srv
    return cfg


def _load_file(path: Path) -> dict[str, _RawServer]:
    """读取一个配置文件 → {server名: _RawServer}；缺失 / 非法 → 空 dict + stderr 告警。"""
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        print(f"[mcp] warn: load {path} failed: {e}", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print(f"[mcp] warn: load {path} failed: 顶层应为映射", file=sys.stderr)
        return {}
    servers = raw.get("mcp_servers")
    if not isinstance(servers, dict):
        return {}
    out: dict[str, _RawServer] = {}
    for name, item in servers.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            continue  # 非法的 server 条目直接忽略（F2 兜底）
        out[name] = _RawServer(
            type=_str_or(item.get("type"), ""),
            command=_str_or(item.get("command"), ""),
            args=[x for x in item.get("args", []) if isinstance(x, str)],
            env=_str_map(item.get("env")),
            url=_str_or(item.get("url"), ""),
            headers=_str_map(item.get("headers")),
        )
    return out


def _str_or(v: object, default: str) -> str:
    """宽松读取字符串字段：非 str 用默认值（字段校验在 _validate_server 兜底）。"""
    return v if isinstance(v, str) else default


def _str_map(v: object) -> dict[str, str]:
    """宽松读取字符串 map：非 {str: str} 的项忽略。"""
    if not isinstance(v, dict):
        return {}
    return {k: val for k, val in v.items() if isinstance(k, str) and isinstance(val, str)}


def _expand_vars(s: str) -> tuple[str, list[str]]:
    """展开 `${VAR}`：已定义取宿主环境值，未定义展开为空串并记录（F3/N2）。"""

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in os.environ:
            return ""
        return os.environ[name]

    undefined = [m.group(1) for m in _VAR_RE.finditer(s) if m.group(1) not in os.environ]
    return _VAR_RE.sub(_sub, s), undefined


def _apply_expansion(name: str, srv: _RawServer) -> None:
    """对单个 server 的 env / headers 值做 `${VAR}` 展开；未定义变量一次性告警（F3）。"""
    undefined: list[str] = []
    for section in ("env", "headers"):
        table: dict[str, str] = getattr(srv, section)
        for key, value in list(table.items()):
            expanded, undef = _expand_vars(value)
            table[key] = expanded
            undefined.extend(undef)
    for v in dict.fromkeys(undefined):  # 同 server 同变量跨 env/headers 限一次
        print(
            f"[mcp] warn: undefined env var ${{{v}}} referenced by server {name}",
            file=sys.stderr,
        )


def _merge_servers(
    user: dict[str, _RawServer], project: dict[str, _RawServer]
) -> dict[str, _RawServer]:
    """按 server 名合并；项目级同名 server 完整覆盖用户级（F1）。"""
    merged = dict(user)
    merged.update(project)
    return merged


def _validate_server(name: str, srv: _RawServer) -> ServerConfig | None:
    """字段校验：type 非法、stdio 缺 command、http 缺 url → 跳过该 server + 告警（F2/N2）。"""
    if srv.type not in ("stdio", "http"):
        print(
            f"[mcp] warn: skip server {name}: type 必须为 stdio 或 http（实际 {srv.type!r}）",
            file=sys.stderr,
        )
        return None
    if srv.type == "stdio" and not srv.command:
        print(f"[mcp] warn: skip server {name}: stdio 类型缺少 command", file=sys.stderr)
        return None
    if srv.type == "http" and not srv.url:
        print(f"[mcp] warn: skip server {name}: http 类型缺少 url", file=sys.stderr)
        return None
    return ServerConfig(
        type=cast(Literal["stdio", "http"], srv.type),
        command=srv.command,
        args=list(srv.args),
        env=dict(srv.env),
        url=srv.url,
        headers=dict(srv.headers),
    )
