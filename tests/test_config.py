"""config 模块单测。"""

import pytest

from novacode.config import ConfigError, ProviderConfig, effective_context_window, load


def _write(tmp_path, text, name="config.yaml"):
    """把文本写入临时配置文件，返回路径字符串。"""
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_valid_single(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: 主模型
    protocol: anthropic
    api_key: sk-ant-test
    model: claude-sonnet-5
    thinking: true
""",
    )
    cfg = load(path)
    assert len(cfg.providers) == 1
    p = cfg.providers[0]
    assert p.name == "主模型"
    assert p.protocol == "anthropic"
    assert p.api_key == "sk-ant-test"
    assert p.model == "claude-sonnet-5"
    assert p.thinking is True
    assert p.base_url is None


def test_load_valid_multiple_with_base_url(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: anthropic
    api_key: k1
    model: m1
  - name: B
    protocol: openai
    api_key: k2
    model: m2
    base_url: https://example.com/v1
    thinking: false
""",
    )
    cfg = load(path)
    assert len(cfg.providers) == 2
    assert cfg.providers[1].protocol == "openai"
    assert cfg.providers[1].base_url == "https://example.com/v1"
    assert cfg.providers[1].thinking is False


def test_missing_api_key(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: anthropic
    model: m1
""",
    )
    with pytest.raises(ConfigError) as ei:
        load(path)
    assert "api_key" in str(ei.value)


def test_missing_name(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - protocol: anthropic
    api_key: k
    model: m
""",
    )
    with pytest.raises(ConfigError) as ei:
        load(path)
    assert "name" in str(ei.value)


def test_invalid_protocol(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: gemini
    api_key: k
    model: m
""",
    )
    with pytest.raises(ConfigError) as ei:
        load(path)
    assert "protocol" in str(ei.value)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load(str(tmp_path / "nope.yaml"))
    assert "不存在" in str(ei.value)


def test_invalid_yaml(tmp_path):
    path = _write(tmp_path, "providers: [unclosed")
    with pytest.raises(ConfigError) as ei:
        load(path)
    assert "解析失败" in str(ei.value)


def test_empty_providers(tmp_path):
    path = _write(tmp_path, "providers: []")
    with pytest.raises(ConfigError):
        load(path)


# ─────────────────────────── ch08 context_window ───────────────────────────


def test_load_context_window_decoded(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: anthropic
    api_key: k
    model: m
    context_window: 80000
""",
    )
    cfg = load(path)
    assert cfg.providers[0].context_window == 80000


def test_load_context_window_default_zero(tmp_path):
    path = _write(
        tmp_path,
        "providers:\n  - name: A\n    protocol: openai\n    api_key: k\n    model: m\n",
    )
    cfg = load(path)
    assert cfg.providers[0].context_window == 0


def test_effective_context_window_unconfigured():
    p = ProviderConfig(name="c", protocol="anthropic", api_key="k", model="m")
    assert effective_context_window(p) == 200000


def test_effective_context_window_zero_openai():
    p = ProviderConfig(name="o", protocol="openai", api_key="k", model="m", context_window=0)
    assert effective_context_window(p) == 128000


def test_effective_context_window_positive():
    p = ProviderConfig(name="c", protocol="anthropic", api_key="k", model="m", context_window=80000)
    assert effective_context_window(p) == 80000


def test_effective_context_window_unknown_protocol():
    p = ProviderConfig(name="x", protocol="weird", api_key="k", model="m")
    assert effective_context_window(p) == 200000


# ─────────────────────────── ch13 enable_subagent_background ───────────────────────────


def test_enable_subagent_background_default_true(tmp_path):
    path = _write(
        tmp_path, "providers:\n  - name: A\n    protocol: anthropic\n    api_key: k\n    model: m\n"
    )
    cfg = load(path)
    assert cfg.enable_subagent_background is None
    assert cfg.effective_enable_subagent_background() is True


def test_enable_subagent_background_false(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: anthropic
    api_key: k
    model: m
enableSubAgentBackground: false
""",
    )
    cfg = load(path)
    assert cfg.enable_subagent_background is False
    assert cfg.effective_enable_subagent_background() is False


def test_enable_subagent_background_true(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: A
    protocol: anthropic
    api_key: k
    model: m
enableSubAgentBackground: true
""",
    )
    cfg = load(path)
    assert cfg.effective_enable_subagent_background() is True
