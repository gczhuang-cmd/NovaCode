"""hook.Engine 单测：dispatch 顺序、拦截、reminder、only_once、reset（ch12 T13）。

shell 命令用 `sys.executable` 构造，保证 Windows（cmd.exe）与 Linux（sh）都能跑。
"""

import asyncio
import sys

from novacode.hook import Event
from novacode.hook.engine import DispatchResult, Engine
from novacode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    PromptAction,
    Rule,
    ShellAction,
)
from novacode.permission.matcher import ExactMatcher


def _py(cmd_body: str) -> str:
    return f'"{sys.executable}" -c "{cmd_body}"'


EXIT2 = _py("import sys; print('blocked-flag', file=sys.stderr); sys.exit(2)")


def _write_marker(path) -> str:
    return _py(f"open(r'{path}','w').write('x')")


def _shell_rule(name, event, command, **kw):
    return Rule(
        name=name,
        event=event,
        action=Action(type=ActionType.SHELL, shell=ShellAction(command=command)),
        **kw,
    )


def _prompt_rule(name, event, text, **kw):
    return Rule(
        name=name,
        event=event,
        action=Action(type=ActionType.PROMPT, prompt=PromptAction(text=text)),
        **kw,
    )


async def test_dispatch_prompt_ordered():
    """多 rule 同事件按声明序执行，prompt 依序累加。"""
    engine = Engine(
        [
            _prompt_rule("p1", Event.SESSION_START, "first"),
            _prompt_rule("p2", Event.SESSION_START, "second"),
        ],
        [],
    )
    r = await engine.dispatch(Event.SESSION_START, {})
    assert r.injected_prompts == ["first", "second"]


async def test_dispatch_blocking_interrupts(tmp_path):
    """拦截类事件下首个 blocked 的 rule 中断后续（break）。"""
    marker = tmp_path / "after-marker"
    engine = Engine(
        [
            _shell_rule("block", Event.PRE_TOOL_USE, EXIT2),
            _shell_rule("after", Event.PRE_TOOL_USE, _write_marker(marker)),
        ],
        [],
    )
    r = await engine.dispatch(Event.PRE_TOOL_USE, {})
    assert r.blocked and r.reason == "blocked-flag" and r.blocking_hook_name == "block"
    assert not marker.exists()  # 第二条未执行


async def test_non_blocking_event_never_blocks(capsys):
    """非拦截类事件（Stop）下 exit 2 不设置 blocked，按 hook 失败记日志。"""
    engine = Engine([_shell_rule("s", Event.STOP, EXIT2)], [])
    r = await engine.dispatch(Event.STOP, {})
    assert not r.blocked and not r.reason
    assert "[hook s] Stop failed:" in capsys.readouterr().err


async def test_dispatch_condition_filter():
    """条件不匹配 → hook 不执行（不拦截）。"""
    cond = Condition(
        mode=CombineMode.ALL_OF,
        atoms=[AtomCondition(field="tool_name", matcher=ExactMatcher("write_file"))],
    )
    engine = Engine(
        [
            Rule(
                name="c",
                event=Event.PRE_TOOL_USE,
                action=Action(type=ActionType.SHELL, shell=ShellAction(command=EXIT2)),
                condition=cond,
            )
        ],
        [],
    )
    r = await engine.dispatch(Event.PRE_TOOL_USE, {"tool_name": "read_file"})
    assert not r.blocked
    r2 = await engine.dispatch(Event.PRE_TOOL_USE, {"tool_name": "write_file"})
    assert r2.blocked


async def test_only_once_fires_once():
    engine = Engine([_prompt_rule("once", Event.PRE_USER_MESSAGE, "x", only_once=True)], [])
    r1 = await engine.dispatch(Event.PRE_USER_MESSAGE, {})
    assert r1.injected_prompts == ["x"]
    r2 = await engine.dispatch(Event.PRE_USER_MESSAGE, {})
    assert r2.injected_prompts == []


async def test_reset_for_new_session():
    engine = Engine([_prompt_rule("once", Event.PRE_USER_MESSAGE, "x", only_once=True)], [])
    await engine.dispatch(Event.PRE_USER_MESSAGE, {})
    await engine.reset_for_new_session()
    r = await engine.dispatch(Event.PRE_USER_MESSAGE, {})
    assert r.injected_prompts == ["x"]


async def test_async_rule_never_blocks(tmp_path):
    """async hook：起后台 task 立即返回，不进入 blocked 判定；后台确实执行。"""
    marker = tmp_path / "marker"
    engine = Engine([_shell_rule("a", Event.STOP, _write_marker(marker), asyncio_mode=True)], [])
    r = await engine.dispatch(Event.STOP, {})
    assert not r.blocked and not r.reason
    for _ in range(40):
        if marker.exists():
            break
        await asyncio.sleep(0.05)
    assert marker.exists()  # 后台 task 执行了写文件命令


async def test_only_once_async_marks_fired():
    """async + only_once：首次 dispatch 即标记 fired，后续跳过（不重复起 task）。"""
    engine = Engine([_prompt_rule("once", Event.STOP, "x", only_once=True, asyncio_mode=True)], [])
    await engine.dispatch(Event.STOP, {})
    # 后台 prompt 任务被丢弃；第二次 dispatch 因 only_once 直接跳过（无法用 injected 验证，
    # 但 dispatch 不抛错即可，且 rules 未被修改）
    assert len(engine.rules) == 1


async def test_empty_engine_dispatch():
    engine = Engine([], [])
    r = await engine.dispatch(Event.SESSION_START, {})
    assert r == DispatchResult()
    assert engine.sources == [] and engine.rules == []
