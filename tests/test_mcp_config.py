"""MCP 配置加载测试：两层合并 / ${VAR} 展开 / 字段校验 / 降级（F1/F2/F3/N2）。"""

from pathlib import Path

from novacode.mcp import Config, load_config


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """把 Path.home 指向 tmp_path，隔离真实用户配置（~/.novacode/config.yaml）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


def test_no_files_is_empty(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    cfg = load_config(str(tmp_path))
    assert isinstance(cfg, Config)
    assert cfg.servers == {}


def test_user_only(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _write(
        tmp_path / ".novacode" / "config.yaml",
        """
mcp_servers:
  github:
    type: stdio
    command: npx
""",
    )
    cfg = load_config(str(tmp_path))
    assert set(cfg.servers) == {"github"}
    assert cfg.servers["github"].type == "stdio"
    assert cfg.servers["github"].command == "npx"


def test_project_only(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _write(
        tmp_path / ".novacode.yaml",
        """
mcp_servers:
  local:
    type: stdio
    command: python
""",
    )
    cfg = load_config(str(tmp_path))
    assert set(cfg.servers) == {"local"}


def test_project_overrides_user_same_name(tmp_path, monkeypatch):
    """同名 server 项目级完整覆盖用户级（AC1/F1）。"""
    _isolate_home(tmp_path, monkeypatch)
    _write(
        tmp_path / ".novacode" / "config.yaml",
        """
mcp_servers:
  demo:
    type: stdio
    command: npx
    args: ["-y", "user-version"]
""",
    )
    _write(
        tmp_path / ".novacode.yaml",
        """
mcp_servers:
  demo:
    type: stdio
    command: npx
    args: ["-y", "project-version"]
  other:
    type: stdio
    command: node
""",
    )
    cfg = load_config(str(tmp_path))
    assert set(cfg.servers) == {"demo", "other"}
    assert cfg.servers["demo"].args == ["-y", "project-version"]  # 项目级完整覆盖


def test_invalid_yaml_degrades(tmp_path, monkeypatch, capsys):
    """文件格式非法 → 跳过该层 + stderr 告警，不抛异常（AC1/N1）。"""
    _isolate_home(tmp_path, monkeypatch)
    _write(tmp_path / ".novacode.yaml", "mcp_servers: [unclosed")
    cfg = load_config(str(tmp_path))
    assert cfg.servers == {}
    assert "[mcp] warn" in capsys.readouterr().err


def test_expand_vars_defined_undefined_command_not_expanded(tmp_path, monkeypatch, capsys):
    """env/headers 的值展开；未定义展开为空串 + 告警；command/args 保留字面量（AC3）。"""
    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.delenv("MISSING_VAR", raising=False)
    _write(
        tmp_path / ".novacode.yaml",
        """
mcp_servers:
  github:
    type: stdio
    command: npx
    args: ["${NOT_EXPANDED}"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
      MISSING: "${MISSING_VAR}"
    headers:
      Authorization: "Bearer ${MISSING_VAR}"
""",
    )
    cfg = load_config(str(tmp_path))
    srv = cfg.servers["github"]
    assert srv.env["GITHUB_TOKEN"] == "secret-token"
    assert srv.env["MISSING"] == ""  # 未定义 → 空串
    assert srv.headers["Authorization"] == "Bearer "  # 未定义 → 空串
    assert srv.args == ["${NOT_EXPANDED}"]  # command/args 不展开
    # MISSING_VAR 在 env 与 headers 各出现一次，告警应去重为一条
    assert capsys.readouterr().err.count("MISSING_VAR") == 1


def test_validate_skip_bad_servers(tmp_path, monkeypatch, capsys):
    """type 非法 / stdio 缺 command / http 缺 url → 跳过 + 告警，其余不受影响（AC2/N2）。"""
    _isolate_home(tmp_path, monkeypatch)
    _write(
        tmp_path / ".novacode.yaml",
        """
mcp_servers:
  bad-type:
    type: sse
  no-command:
    type: stdio
  no-url:
    type: http
    headers:
      Authorization: "Bearer x"
  good:
    type: stdio
    command: python
""",
    )
    cfg = load_config(str(tmp_path))
    assert set(cfg.servers) == {"good"}
    err = capsys.readouterr().err
    assert "bad-type" in err and "no-command" in err and "no-url" in err


def test_example_yaml_parses(tmp_path, monkeypatch, capsys):
    """T6：示例配置文件三个 server 全部解析成功（AC1/AC3）。"""
    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("EXAMPLE_TOKEN", "y")
    example = Path(__file__).resolve().parents[1] / "docs" / "ch07" / "mcp-servers.example.yaml"
    _write(tmp_path / ".novacode.yaml", example.read_text(encoding="utf-8"))
    cfg = load_config(str(tmp_path))
    assert set(cfg.servers) == {"github", "local-sqlite", "example-http"}
    assert cfg.servers["github"].env["GITHUB_TOKEN"] == "x"
    assert cfg.servers["example-http"].headers["Authorization"] == "Bearer y"
    assert "undefined" not in capsys.readouterr().err
