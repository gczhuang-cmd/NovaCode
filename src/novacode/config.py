"""配置层：读取并校验 `.novacode/config.yaml`，产出 providers 列表。

对外接口：
- `load(path) -> Config`：加载 + 校验，失败抛 `ConfigError`（携带可读信息）。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

# 支持的协议类型
ProtocolName = Literal["anthropic", "openai"]

# 协议默认上下文窗口（token）——未配置 context_window 时按 protocol 生效（ch08 F31）
DEFAULT_ANTHROPIC_CONTEXT_WINDOW = 200000
DEFAULT_OPENAI_CONTEXT_WINDOW = 128000


class ConfigError(Exception):
    """配置错误：信息可读、非崩溃堆栈。"""


@dataclass
class ProviderConfig:
    """单个 provider 的配置。"""

    name: str  # 状态栏左侧显示
    protocol: ProtocolName  # 协议类型
    api_key: str
    model: str  # 状态栏右侧显示
    base_url: str | None = None  # None 则用 SDK 默认端点
    thinking: bool = False  # 仅 anthropic 生效
    context_window: int = 0  # 可选：上下文窗口（token）；0 表示按协议默认（ch08 F30）


@dataclass
class FeaturesConfig:
    """ch15：可选 feature 开关（YAML 顶层 `features:` 段，snake_case）。"""

    coordinator_mode: bool = False  # 双锁之一（另一把是 novaCODE_COORDINATOR_MODE 环境变量）
    fork_teammate: bool = False  # 队员 Fork 路径开关（默认关，spec G11）


@dataclass
class Config:
    """整体配置。"""

    providers: list[ProviderConfig] = field(default_factory=list)
    # ch13：SubAgent 后台开关（YAML key: enableSubAgentBackground）。None = 默认 True。
    enable_subagent_background: bool | None = None
    # ch15：feature 开关（coordinator_mode / fork_teammate）
    features: FeaturesConfig = field(default_factory=FeaturesConfig)

    def effective_enable_subagent_background(self) -> bool:
        """SubAgent 后台开关生效值：缺省 True；False 时所有子 Agent 强制前台（N6）。"""
        return True if self.enable_subagent_background is None else self.enable_subagent_background


def load(path: str) -> Config:
    """加载并校验配置文件。

    文件不存在、YAML 解析失败、必要字段缺失或非法时抛 `ConfigError`。
    """
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        text = p.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"配置文件解析失败: {path}: {e}") from e
    return _from_dict(raw)


def _from_dict(raw: object) -> Config:
    """把解析后的 dict 映射到 dataclass，并逐项校验。"""
    if not isinstance(raw, dict):
        raise ConfigError("配置文件格式错误: 顶层应为映射，包含非空的 providers 列表")
    provs = raw.get("providers")
    if not isinstance(provs, list) or not provs:
        raise ConfigError("配置错误: providers 列表不能为空")
    cfg = Config()
    for i, item in enumerate(provs):
        cfg.providers.append(_provider_from_dict(item, i))
    # ch13：enableSubAgentBackground（顶层，可选；缺省 None → effective 返回 True）
    bsv = raw.get("enableSubAgentBackground")
    if bsv is not None:
        cfg.enable_subagent_background = _to_bool(bsv, 0, "enableSubAgentBackground")
    # ch15：features 段（可选，snake_case 字段）
    feat = raw.get("features")
    if isinstance(feat, dict):
        f = FeaturesConfig()
        for k, v in feat.items():
            if k in ("coordinator_mode", "fork_teammate"):
                setattr(f, k, _to_bool(v, 0, f"features.{k}"))
        cfg.features = f
    return cfg


def _required(item: dict, i: int, key: str) -> str:
    """读取必填字符串字段，缺失/空白时抛可读错误。"""
    v = item.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"providers[{i}].{key} 不能为空")
    return v.strip()


def _to_int(v: object, i: int, key: str) -> int:
    """宽松读取整数字段：非负 int 返回，其余返回 0。"""
    if isinstance(v, bool):
        return 0
    if isinstance(v, int) and v >= 0:
        return v
    if isinstance(v, float) and v.is_integer() and v >= 0:
        return int(v)
    return 0


def _to_bool(v: object, i: int, key: str) -> bool:
    """宽松地把 YAML 布尔/字符串转成 bool。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(v, (int, float)):
        return bool(v)
    raise ConfigError(f"providers[{i}].{key} 应为布尔值")


def _provider_from_dict(item: object, i: int) -> ProviderConfig:
    if not isinstance(item, dict):
        raise ConfigError(f"providers[{i}] 应为映射")
    protocol = _required(item, i, "protocol")
    if protocol not in ("anthropic", "openai"):
        raise ConfigError(f"providers[{i}].protocol 非法: {protocol}（仅支持 anthropic / openai）")
    base_url = item.get("base_url")
    return ProviderConfig(
        name=_required(item, i, "name"),
        protocol=protocol,
        api_key=_required(item, i, "api_key"),
        model=_required(item, i, "model"),
        base_url=base_url.strip() if isinstance(base_url, str) and base_url.strip() else None,
        thinking=_to_bool(item.get("thinking", False), i, "thinking"),
        context_window=_to_int(item.get("context_window", 0), i, "context_window"),
    )


def effective_context_window(p: ProviderConfig) -> int:
    """返回 provider 生效的上下文窗口：配置 > 0 用配置，否则按协议默认（ch08 F31）。"""
    if p.context_window > 0:
        return p.context_window
    if p.protocol == "anthropic":
        return DEFAULT_ANTHROPIC_CONTEXT_WINDOW
    if p.protocol == "openai":
        return DEFAULT_OPENAI_CONTEXT_WINDOW
    return DEFAULT_ANTHROPIC_CONTEXT_WINDOW
