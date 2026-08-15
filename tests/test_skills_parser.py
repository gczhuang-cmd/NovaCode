"""SKILL.md / tool.json 解析单测（ch11 T3）。"""

from __future__ import annotations

import json

import pytest

from novacode.skills.parser import parse_frontmatter_and_body, parse_skill_dir
from novacode.skills.types import SkillSource


def _write_skill(tmp_path, name="foo", extra_fm="", body="正文内容"):
    d = tmp_path / name
    d.mkdir(parents=True)
    fm = f"name: {name}\ndescription: 一句话描述\n{extra_fm}"
    (d / "SKILL.md").write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
    return d


def test_parse_skill_dir_minimal(tmp_path):
    """最简 SKILL.md（name+description）解析成功。"""
    d = _write_skill(tmp_path)
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.name == "foo"
    assert s.meta.description == "一句话描述"
    assert s.meta.mode == "inline"  # 缺省
    assert s.meta.allowed_tools == []
    assert s.prompt_body == "正文内容"
    assert s.source == SkillSource.USER
    assert s.source_dir == d.resolve()


def test_parse_skill_dir_all_fields(tmp_path):
    """frontmatter 全字段解析。"""
    d = _write_skill(
        tmp_path,
        extra_fm="allowed_tools: [bash, read_file]\nmode: fork\nfork_context: recent\nmodel: deepseek",
    )
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.allowed_tools == ["bash", "read_file"]
    assert s.meta.is_fork()
    assert s.meta.fork_context == "recent"
    assert s.meta.model == "deepseek"


def test_parse_skill_dir_invalid_name(tmp_path):
    """name 含大写/非法字符 → ValueError（F3）。"""
    d = _write_skill(tmp_path, name="BadName")
    with pytest.raises(ValueError, match="name"):
        parse_skill_dir(d, SkillSource.USER)
    d2 = _write_skill(tmp_path, name="has space")
    with pytest.raises(ValueError, match="name"):
        parse_skill_dir(d2, SkillSource.USER)


def test_parse_skill_dir_missing_description(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError, match="description"):
        parse_skill_dir(d, SkillSource.USER)


def test_parse_skill_dir_no_skill_md(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        parse_skill_dir(d, SkillSource.USER)


def test_parse_skill_dir_unknown_mode_warns(tmp_path):
    """非法 mode → 按 inline 处理（warning 不打乱测试）。"""
    d = _write_skill(tmp_path, extra_fm="mode: weird")
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.mode == "inline"


def test_parse_skill_dir_with_tool_json(tmp_path):
    """合法 tool.json 解析为 ToolSpec 列表。"""
    d = _write_skill(tmp_path)
    (d / "tool.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "parse_resume",
                        "description": "解析简历",
                        "input_schema": {"type": "object", "properties": {}},
                        "command": ["parse_resume.sh", "arg"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    s = parse_skill_dir(d, SkillSource.USER)
    assert len(s.tool_specs) == 1
    assert s.tool_specs[0].name == "parse_resume"
    assert s.tool_specs[0].command == ["parse_resume.sh", "arg"]
    assert s.tool_specs[0].base_dir == d.resolve()


def test_parse_skill_dir_invalid_tool_json(tmp_path):
    d = _write_skill(tmp_path)
    (d / "tool.json").write_text('{"tools": [{"name": "Bad_Name"}]}', encoding="utf-8")
    with pytest.raises(ValueError):
        parse_skill_dir(d, SkillSource.USER)


def test_parse_frontmatter_and_body():
    meta, body = parse_frontmatter_and_body("---\nname: foo\n---\n\n正文\n第二行")
    assert meta == {"name": "foo"}
    assert body == "正文\n第二行"


def test_parse_frontmatter_missing_delimiter():
    with pytest.raises(ValueError):
        parse_frontmatter_and_body("name: foo\n")


# ─────────────────────────── ch11 加固（第三方 skill 兼容） ───────────────────────────


def test_parse_skill_dir_bom(tmp_path):
    """SKILL.md 带 BOM（PowerShell/Windows 落盘常见）→ 仍解析成功。"""
    d = tmp_path / "foo"
    d.mkdir()
    (d / "SKILL.md").write_text("﻿---\nname: foo\ndescription: d\n---\nbody\n", encoding="utf-8")
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.name == "foo"
    assert s.prompt_body.strip() == "body"


def test_parse_skill_dir_crlf(tmp_path):
    """CRLF 换行的 SKILL.md（Windows 文件）→ 仍解析成功。"""
    d = tmp_path / "foo"
    d.mkdir()
    (d / "SKILL.md").write_bytes(b"---\r\nname: foo\r\ndescription: d\r\n---\r\nbody\r\n")
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.name == "foo"
    assert "body" in s.prompt_body


def test_parse_skill_dir_yaml_error_raises_valueerror(tmp_path):
    """坏 YAML 抛 ValueError（而非未捕获的 ParserError，避免 Catalog 崩溃）。"""
    d = tmp_path / "foo"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: [foo\ndescription: d\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        parse_skill_dir(d, SkillSource.USER)


def test_parse_allowed_tools_hyphen(tmp_path):
    """skills.sh/anthropics 规范用连字符 `allowed-tools` → 正确解析到白名单。"""
    d = _write_skill(tmp_path, extra_fm="allowed-tools: [bash, read_file]\n")
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.allowed_tools == ["bash", "read_file"]


def test_parse_skill_name_33_to_64_chars(tmp_path):
    """name 33-64 字符（市场允许 ~64，原上限 32 会误拒）→ 解析成功。"""
    long_name = "a" * 40
    d = _write_skill(tmp_path, name=long_name)
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.name == long_name


def test_parse_multiline_description(tmp_path):
    """description 用 YAML 块标量 `|-`（多行）→ 保留为多行字符串。"""
    d = tmp_path / "foo"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: foo\ndescription: |-\n  第一行\n  第二行\n---\nbody\n",
        encoding="utf-8",
    )
    s = parse_skill_dir(d, SkillSource.USER)
    assert s.meta.description == "第一行\n第二行"
