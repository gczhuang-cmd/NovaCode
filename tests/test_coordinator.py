"""Coordinator 双锁测试（ch15 T24，spec F52/F53）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from novacode.config import FeaturesConfig
from novacode.coordinator import COORDINATOR_ALLOWED_TOOLS, env_truthy, is_enabled


@dataclass
class FakeCfg:
    features: FeaturesConfig = field(default_factory=FeaturesConfig)


def test_env_truthy():
    assert env_truthy("1")
    assert env_truthy("TRUE")
    assert env_truthy("Yes")
    assert env_truthy("on")
    assert not env_truthy("0")
    assert not env_truthy("false")
    assert not env_truthy("")


def test_double_lock(monkeypatch):
    # 00：feature 关 + env 关
    monkeypatch.delenv("novaCODE_COORDINATOR_MODE", raising=False)
    assert not is_enabled(FakeCfg())
    # 01：feature 关 + env 开 → False
    monkeypatch.setenv("novaCODE_COORDINATOR_MODE", "1")
    assert not is_enabled(FakeCfg())
    # 10：feature 开 + env 关 → False
    monkeypatch.delenv("novaCODE_COORDINATOR_MODE", raising=False)
    assert not is_enabled(FakeCfg(FeaturesConfig(coordinator_mode=True)))
    # 11：两把锁全开 → True
    monkeypatch.setenv("novaCODE_COORDINATOR_MODE", "1")
    assert is_enabled(FakeCfg(FeaturesConfig(coordinator_mode=True)))


def test_allowed_tools():
    allowed = COORDINATOR_ALLOWED_TOOLS
    assert "bash" in allowed
    assert "read_file" in allowed and "grep" in allowed
    assert "write_file" not in allowed and "edit_file" not in allowed  # 剥夺写工具（F53）
    assert "TeamSendMessage" in allowed and "TeamTaskCreate" in allowed
    assert "Agent" in allowed and "TeamCreate" in allowed
