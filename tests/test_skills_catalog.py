"""Catalog 三层加载与覆盖单测（ch11 T6）。"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from novacode.skills import Catalog
from novacode.skills.types import SkillSource


def _write_skill(base: Path, name: str, description: str) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n", encoding="utf-8"
    )
    return d


class _KnownReg:
    """认识内置工具的假 registry：内置 Skill 的 allowed_tools 不报 issue。"""

    BUILTIN_TOOLS: ClassVar[set[str]] = {
        "bash",
        "read_file",
        "grep",
        "glob",
        "write_file",
        "edit_file",
    }

    def get(self, name):
        return object() if name in self.BUILTIN_TOOLS else None


def test_load_catalog_builtin_only(tmp_path, monkeypatch):
    """空 work_dir + 空 HOME → 仅三个内置 Skill（字典序）。"""
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: tmp_path)
    c = Catalog.load(tmp_path)
    assert c.names() == ["commit", "review", "test"]


def test_load_catalog_user_override(tmp_path, monkeypatch):
    """用户目录同名 Skill 覆盖内置。"""
    home = tmp_path / "home"
    _write_skill(home / ".novacode" / "skills", "commit", "用户版 commit")
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: home)
    c = Catalog.load(tmp_path)
    commit = c.get("commit")
    assert commit is not None
    assert commit.meta.description == "用户版 commit"
    assert commit.source == SkillSource.USER


def test_load_catalog_project_override(tmp_path, monkeypatch):
    """项目目录同名 Skill 覆盖用户。"""
    home = tmp_path / "home"
    _write_skill(home / ".novacode" / "skills", "commit", "用户版 commit")
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: home)
    _write_skill(tmp_path / ".novacode" / "skills", "commit", "项目版 commit")
    c = Catalog.load(tmp_path)
    assert c.get("commit").meta.description == "项目版 commit"
    assert c.get("commit").source == SkillSource.PROJECT


def test_load_catalog_skips_broken_skill(tmp_path, monkeypatch, capsys):
    """单个损坏 Skill 只跳过本身，其它仍加载。"""
    home = tmp_path / "home"
    skills_dir = home / ".novacode" / "skills"
    _write_skill(skills_dir, "good", "好 Skill")
    bad = skills_dir / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("name: Bad\n", encoding="utf-8")  # 非法 name + 无闭 frontmatter
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: home)
    c = Catalog.load(tmp_path)
    assert "good" in c.names()
    assert "bad" not in c.names()
    assert "warn" in capsys.readouterr().err


def test_validate_tools_missing_tool(tmp_path, monkeypatch):
    """allowed_tools 引用不存在工具 → 返回 1 个 issue。"""
    home = tmp_path / "home"
    skills_dir = home / ".novacode" / "skills"
    _write_skill(skills_dir, "foo", "x")
    # 覆写 SKILL.md 加 NotExist
    (skills_dir / "foo" / "SKILL.md").write_text(
        "---\nname: foo\ndescription: x\nallowed_tools: [NotExist]\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: home)
    c = Catalog.load(tmp_path)

    issues = c.validate_tools(_KnownReg())  # 内置工具已认识，只有 foo/NotExist 报 issue
    assert len(issues) == 1
    assert issues[0].skill_name == "foo"
    assert issues[0].tool_name == "NotExist"


def test_validate_tools_system_tools_allowed(tmp_path, monkeypatch):
    """load_skill / install_skill 视为允许引用。"""
    home = tmp_path / "home"
    skills_dir = home / ".novacode" / "skills"
    _write_skill(skills_dir, "foo", "x")
    (skills_dir / "foo" / "SKILL.md").write_text(
        "---\nname: foo\ndescription: x\nallowed_tools: [load_skill, install_skill]\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: home)
    c = Catalog.load(tmp_path)

    assert c.validate_tools(_KnownReg()) == []  # 内置工具已认识 + 系统工具豁免，无 issue


def test_load_catalog_bad_yaml_skipped_no_crash(tmp_path, monkeypatch):
    """ch11 加固回归：坏 YAML 的第三方 skill 被跳过、不导致 Catalog 崩溃。"""
    home = tmp_path / "home"
    d = home / ".novacode" / "skills" / "badskill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: [foo\ndescription: d\n---\nbody", encoding="utf-8")
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: home)
    c = Catalog.load(tmp_path)  # 不应抛异常
    assert "badskill" not in c.names()
    assert "commit" in c.names()  # 内置 skill 不受影响


def test_catalog_remove(tmp_path, monkeypatch):
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: tmp_path)
    c = Catalog.load(tmp_path)
    c.remove("commit")
    assert "commit" not in c.names()
    assert c.get("commit") is None


def test_catalog_reload_returns_diff(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: home)
    c = Catalog.load(tmp_path)
    added, removed = c.reload(tmp_path)
    assert added == [] and removed == []
    # 加一个 user skill 后 reload
    _write_skill(home / ".novacode" / "skills", "newskill", "新")
    added, removed = c.reload(tmp_path)
    assert "newskill" in added
    assert c.get("newskill") is not None
