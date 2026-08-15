"""subagent Catalog 三层加载与覆盖测试（ch13 T7）。"""

from __future__ import annotations

from novacode.subagent import catalog as catalog_mod
from novacode.subagent.catalog import Catalog, load_catalog
from novacode.subagent.definition import Source

_EXPLORE_PROJECT = """---
name: Explore
description: 项目级覆盖的 Explore
maxTurns: 10
---

你是项目级覆盖的 Explore。
"""

_EXPLORE_USER = """---
name: Explore
description: 用户级覆盖的 Explore
maxTurns: 8
---

你是用户级覆盖的 Explore。
"""

_BAD = "---\nname: bad\nnot closed"


def _write(dir_path, name: str, content: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(content, encoding="utf-8")


def test_load_all_three_layers(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    home = tmp_path / "home"
    monkeypatch.setattr(catalog_mod, "_home", lambda: home)
    _write(
        project / ".novacode" / "agents", "custom.md", "---\nname: custom\ndescription: c\n---\n"
    )
    _write(home / ".novacode" / "agents", "user.md", "---\nname: userx\ndescription: u\n---\n")
    c = load_catalog(str(project))
    # 内置 3 + 项目 1 + 用户 1
    assert {d.name for d in c.list()} == {"Explore", "Plan", "general-purpose", "custom", "userx"}


def test_project_overrides_user_and_builtin(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    home = tmp_path / "home"
    monkeypatch.setattr(catalog_mod, "_home", lambda: home)
    _write(project / ".novacode" / "agents", "Explore.md", _EXPLORE_PROJECT)
    _write(home / ".novacode" / "agents", "Explore.md", _EXPLORE_USER)
    c = load_catalog(str(project))
    d = c.resolve("Explore")
    assert d is not None
    assert d.source is Source.PROJECT
    assert d.max_turns == 10
    assert "项目级覆盖" in d.system_prompt


def test_user_overrides_builtin(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    home = tmp_path / "home"
    monkeypatch.setattr(catalog_mod, "_home", lambda: home)
    _write(home / ".novacode" / "agents", "Explore.md", _EXPLORE_USER)
    c = load_catalog(str(project))
    d = c.resolve("Explore")
    assert d.source is Source.USER
    assert d.max_turns == 8


def test_builtin_only(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_mod, "_home", lambda: tmp_path / "nohome")
    c = load_catalog(str(tmp_path / "noproj"))
    d = c.resolve("Explore")
    assert d.source is Source.BUILTIN


def test_fork_definition_is_fork():
    c = Catalog()
    fd = c.fork_definition()
    assert fd.is_fork() is True
    assert fd.name == "__fork__"
    assert fd.tools == []
    assert fd.disallowed_tools == []


def test_bad_file_skipped_with_warning(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    home = tmp_path / "home"
    monkeypatch.setattr(catalog_mod, "_home", lambda: home)
    _write(project / ".novacode" / "agents", "bad.md", _BAD)
    _write(project / ".novacode" / "agents", "good.md", "---\nname: good\ndescription: g\n---\n")
    c = load_catalog(str(project))
    assert c.resolve("good") is not None
    assert c.resolve("bad") is None
    err = capsys.readouterr().err
    assert "bad.md" in err
    assert "skipped" in err


def test_list_by_source(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    home = tmp_path / "home"
    monkeypatch.setattr(catalog_mod, "_home", lambda: home)
    _write(
        project / ".novacode" / "agents", "custom.md", "---\nname: custom\ndescription: c\n---\n"
    )
    c = load_catalog(str(project))
    project_names = [d.name for d in c.list_by_source(Source.PROJECT)]
    assert project_names == ["custom"]
    assert "Explore" in [d.name for d in c.list_by_source(Source.BUILTIN)]


def test_import_catalog_from_package():
    from novacode.subagent import Catalog, load_catalog  # noqa: F401

    assert Catalog is catalog_mod.Catalog
