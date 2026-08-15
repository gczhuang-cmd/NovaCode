"""Skill 命令注册与 /skill handler 单测（ch11 T22/T23）。"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from novacode.command import Command, CommandContext, Kind, Registry, register_builtins
from novacode.command.builtin_skill import handle_skill
from novacode.command.skills import register_skills_as_commands, remove_skill_commands
from novacode.skills import Catalog
from novacode.skills.parser import parse_skill_dir
from novacode.skills.types import SkillSource


def _console() -> Console:
    return Console(file=io.StringIO(), highlight=False, width=80)


def _skill_catalog(tmp_path, name="foo", description="描述") -> Catalog:
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n", encoding="utf-8"
    )
    c = Catalog()
    c.register(parse_skill_dir(d, SkillSource.USER))
    return c


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, ctx, name, args):
        self.calls.append((name, args))


def _ctx(catalog=None, console=None, executor=None) -> CommandContext:
    return CommandContext(
        holder=SimpleNamespace(conv=None, writer=None, ses_ctx=None),
        registry=SimpleNamespace(),
        mode_box=SimpleNamespace(mode=None),
        agent=None,
        console=console or _console(),
        exit_flag=[False],
        run_callback=lambda: None,
        provider=None,
        usage_box=None,
        mem_mgr=None,
        runtime=None,
        root="",
        sessions_dir="",
        catalog=catalog,
        executor=executor,
    )


def test_register_skills_as_commands(tmp_path):
    reg = Registry()
    catalog = _skill_catalog(tmp_path, "foo", "描述")
    register_skills_as_commands(reg, catalog)
    cmd = reg.lookup("foo")
    assert cmd is not None
    assert cmd.kind == Kind.PROMPT
    assert cmd.is_skill is True
    assert cmd.description == "描述 [skill]"
    assert cmd.name == "foo"


async def test_skill_command_runs_executor(tmp_path):
    reg = Registry()
    catalog = _skill_catalog(tmp_path, "foo")
    ex = _FakeExecutor()
    register_skills_as_commands(reg, catalog)
    cmd = reg.lookup("foo")
    ctx = _ctx(catalog=catalog, executor=ex)
    await cmd.handler(ctx)
    assert ex.calls == [("foo", "")]


def test_remove_skill_commands_keeps_builtin(tmp_path):
    reg = Registry()
    register_builtins(reg)
    catalog = _skill_catalog(tmp_path, "foo")
    register_skills_as_commands(reg, catalog)
    assert reg.lookup("foo") is not None
    remove_skill_commands(reg)
    assert reg.lookup("foo") is None
    assert reg.lookup("help") is not None  # 内置命令保留


@pytest.mark.asyncio
async def test_handle_skill_lists(tmp_path):
    catalog = _skill_catalog(tmp_path, "foo", "描述")
    ctx = _ctx(catalog=catalog)
    await handle_skill(ctx)
    out = ctx.console.file.getvalue()
    assert "Available skills (1):" in out
    assert "  /foo" in out and "描述" in out
    assert "Type /<skill-name> to invoke a skill." in out


@pytest.mark.asyncio
async def test_handle_skill_empty(tmp_path):
    ctx = _ctx(catalog=Catalog())
    await handle_skill(ctx)
    assert ctx.console.file.getvalue() == "No skills loaded.\n"


def test_registry_remove_if():
    reg = Registry()
    register_builtins(reg)
    reg.register(
        Command(
            name="tmpskill",
            description="x [skill]",
            kind=Kind.PROMPT,
            handler=lambda ctx: None,
            is_skill=True,
        )
    )
    assert reg.lookup("tmpskill") is not None
    reg.remove_if(lambda c: c.is_skill)
    assert reg.lookup("tmpskill") is None
    assert reg.lookup("help") is not None
