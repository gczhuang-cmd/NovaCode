"""InstallSkill 工具：从 URL 安装第三方 Skill（ch11 T17）。"""

from __future__ import annotations

import json

from . import Result


class InstallSkillTool:
    """普通工具：写盘 + 网络，受权限模式约束（F33/N8）。"""

    read_only = False
    is_system = False

    def __init__(self, catalog, work_dir) -> None:
        self._catalog = catalog
        self._work_dir = work_dir

    def name(self) -> str:
        return "install_skill"

    def description(self) -> str:
        return "从 HTTP(S) URL 下载并安装一个 Skill zip 到 ~/.novacode/skills/。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"source": {"type": "string", "description": "URL of a Skill zip"}},
            "required": ["source"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")
        source = data.get("source")
        if not isinstance(source, str) or not source:
            return Result(is_error=True, content="缺少参数 source")
        from ..skills.install import install_from_url

        try:
            top = await install_from_url(source, self._catalog, self._work_dir)
        except ValueError as e:
            return Result(is_error=True, content=str(e))
        except Exception as e:  # noqa: BLE001 网络/解压错误结构化返回
            return Result(is_error=True, content=f"安装失败: {e}")
        return Result(content=f"Skill {top} installed to ~/.novacode/skills/{top}.")
