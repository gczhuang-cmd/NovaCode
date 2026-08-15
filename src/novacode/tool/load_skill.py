"""LoadSkill 工具：按名激活 Skill，把 SOP 钉到 env context（ch11 T16）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import Result
from .skill_tool import new_skill_tool

logger = logging.getLogger(__name__)


class LoadSkillTool:
    """系统工具：只动 Agent 自身激活态，无外部副作用（F23/N4）。"""

    read_only = True  # 不被权限系统拦截
    is_system = True  # 跨任意 allowed_tools 白名单都可见

    def __init__(self, catalog, active, registry) -> None:
        self._catalog = catalog
        self._active = active
        self._registry = registry

    def name(self) -> str:
        return "load_skill"

    def description(self) -> str:
        return (
            "按名激活一个 Skill：把其完整 SOP 钉到环境上下文并注册专属工具。"
            "执行某类任务前先用它加载对应技能。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name to activate"}},
            "required": ["name"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        name = data.get("name")
        if not isinstance(name, str) or not name:
            return Result(is_error=True, content="缺少参数 name")
        skill = self._catalog.get(name)
        if skill is None:
            return Result(is_error=True, content=f"unknown skill: {name}")
        body = self._read_body(skill)
        self._active.activate(skill.meta.name, body)
        n = 0
        for spec in skill.tool_specs:
            self._registry.register_skill_tool(
                new_skill_tool(
                    spec.name, spec.description, spec.input_schema, spec.command, spec.base_dir
                )
            )
            n += 1
        return Result(
            content=(
                f"Skill {name} activated. SOP pinned to env context. "
                f"{n} specialized tools registered."
            )
        )

    def _read_body(self, skill) -> str:
        """重读 SKILL.md 拿最新 body；失败回退缓存并打 warning（N5）。"""
        from ..skills.parser import parse_frontmatter_and_body

        try:
            if skill.source.value == "builtin":
                from importlib.resources import files

                md = files("novacode.skills.builtin").joinpath(skill.meta.name, "SKILL.md")
                _, body = parse_frontmatter_and_body(md.read_text(encoding="utf-8"))
            else:
                md = Path(skill.source_dir) / "SKILL.md"
                _, body = parse_frontmatter_and_body(md.read_text(encoding="utf-8"))
            return body
        except (OSError, ValueError) as e:
            logger.warning("重读 SKILL.md 失败，回退缓存版本: %s", e)
            return skill.prompt_body
