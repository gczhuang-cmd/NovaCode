"""feature flag 读取（ch15 T14，spec G11 FORK_TEAMMATE）。"""

from __future__ import annotations


def fork_teammate_enabled(cfg) -> bool:
    """读 `cfg.features.fork_teammate`（默认 False）。"""
    features = getattr(cfg, "features", None)
    return bool(getattr(features, "fork_teammate", False))
