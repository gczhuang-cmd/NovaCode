"""render_body 单测（ch11 T8）。"""

from __future__ import annotations

from pathlib import Path

from novacode.skills import render_body
from novacode.skills.types import Skill, SkillMeta, SkillSource


def _skill(body: str, allowed: list[str] | None = None) -> Skill:
    return Skill(
        meta=SkillMeta(name="foo", description="x", allowed_tools=allowed or []),
        prompt_body=body,
        source_dir=Path("."),
        source=SkillSource.USER,
    )


def test_render_placeholder_replaced():
    s = _skill("执行：$ARGUMENTS")
    out = render_body(s, "参数文本")
    assert out == "执行：参数文本"


def test_render_no_placeholder_appends_request():
    s = _skill("固定 SOP")
    out = render_body(s, "用户参数")
    assert "固定 SOP" in out
    assert "## User Request" in out
    assert "用户参数" in out


def test_render_empty_args_no_append():
    s = _skill("固定 SOP")
    out = render_body(s, "")
    assert out == "固定 SOP"


def test_render_allowed_tools_hint_top():
    s = _skill("SOP 正文", allowed=["bash", "read_file"])
    out = render_body(s, "")
    assert out.startswith("This skill is designed to use only these tools: bash, read_file.")
    assert "SOP 正文" in out
