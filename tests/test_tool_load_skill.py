"""LoadSkill 工具单测（ch11 T16）。"""

from __future__ import annotations

import asyncio
import json

from novacode.skills import ActiveSkills, Catalog
from novacode.skills.parser import parse_skill_dir
from novacode.skills.types import SkillSource
from novacode.tool import Registry
from novacode.tool.load_skill import LoadSkillTool


def _skill_dir(tmp_path, name="foo", with_tool_json=False):
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nSOP body", encoding="utf-8"
    )
    if with_tool_json:
        (d / "tool.json").write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "name": "parse_resume",
                            "description": "解析简历",
                            "input_schema": {"type": "object"},
                            "command": ["noop.sh"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    return d


def _setup(tmp_path, **kw):
    d = _skill_dir(tmp_path, **kw)
    skill = parse_skill_dir(d, SkillSource.USER)
    catalog = Catalog()
    catalog.register(skill)
    active = ActiveSkills()
    reg = Registry()
    tool = LoadSkillTool(catalog, active, reg)
    return skill, catalog, active, reg, tool


def test_load_skill_activates(tmp_path):
    _skill, _catalog, active, _reg, tool = _setup(tmp_path)
    r = asyncio.run(tool.execute('{"name": "foo"}'))
    assert "activated" in r.content
    assert active.names() == ["foo"]
    # body 是重读的最新 SOP
    assert active.snapshot()[0].body == "SOP body"


def test_load_skill_unknown(tmp_path):
    catalog = Catalog()
    active = ActiveSkills()
    reg = Registry()
    tool = LoadSkillTool(catalog, active, reg)
    r = asyncio.run(tool.execute('{"name": "nope"}'))
    assert r.is_error and "unknown skill: nope" in r.content


def test_load_skill_registers_tools(tmp_path):
    _skill, _catalog, _active, reg, tool = _setup(tmp_path, with_tool_json=True)
    r = asyncio.run(tool.execute('{"name": "foo"}'))
    assert "1 specialized tools registered" in r.content
    assert reg.get("parse_resume") is not None
