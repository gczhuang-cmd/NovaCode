"""内置 Agent 定义加载测试（ch13 T7 的一部分）。"""

from __future__ import annotations

from novacode.subagent.definition import Source
from novacode.subagent.embed import builtin_definitions


def test_builtin_definitions_three():
    defs = builtin_definitions()
    names = [d.name for d in defs]
    assert names == ["Explore", "Plan", "general-purpose"]  # 按 name 升序
    assert all(d.source is Source.BUILTIN for d in defs)


def test_builtin_explore_readonly():
    explore = next(d for d in builtin_definitions() if d.name == "Explore")
    assert "write_file" in explore.disallowed_tools
    assert "edit_file" in explore.disallowed_tools
    assert explore.system_prompt.strip()


def test_builtin_plan_mode():
    plan = next(d for d in builtin_definitions() if d.name == "Plan")
    assert plan.max_turns == 15
    assert plan.permission_mode.value == 2  # Mode.PLAN
    assert "write_file" in plan.disallowed_tools
