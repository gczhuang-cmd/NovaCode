"""agent ReAct 循环单测（fake provider 驱动）：多轮链路、停止条件、保序分批并发、取消收尾、Plan 工具集。"""

import asyncio
import json
import sys
import time

import pytest

from novacode.agent import (
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    CompactPhase,
    Phase,
)
from novacode.conversation import Conversation
from novacode.llm import PromptTooLongError, Request, StreamEvent, ToolCall
from novacode.llm import Usage as LLMUsage
from novacode.permission import Decision, Mode, Outcome, new_engine
from novacode.tool import Registry, Result, new_default_registry


def _engine(tmp_path):
    """以 tmp_path 为根的空规则引擎（沙箱/规则都放行项目内操作）。"""
    engine, err = new_engine(str(tmp_path))
    assert err is None
    return engine


class FakeProvider:
    """脚本化 provider：第 call_count 次请求返回 scripts[call_count-1]，超出重复最后一段。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.call_count = 0
        self.requests: list[Request] = []

    async def stream(self, req: Request):
        self.call_count += 1
        self.requests.append(req)
        idx = min(self.call_count - 1, len(self.scripts) - 1)
        for ev in self.scripts[idx]:
            yield ev


class SleepReadTool:
    """只读插桩：记录同时在跑的并发数峰值与每个完成时刻（可配延迟）。"""

    read_only = True

    def __init__(self, state: dict, delay: float = 0.1, name: str = "ro") -> None:
        self.state = state
        self.delay = delay
        self._name = name

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return "只读插桩"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        s = self.state
        s["current"] += 1
        s["max_peak"] = max(s["max_peak"], s["current"])
        await asyncio.sleep(self.delay)
        s["current"] -= 1
        s["done_at"].append(time.monotonic())
        return Result(content="ro-ok", is_error=False)


class SideEffectTool:
    """有副作用插桩：记录开始时刻。"""

    read_only = False

    def __init__(self, log: list) -> None:
        self.log = log

    def name(self) -> str:
        return "rw"

    def description(self) -> str:
        return "有副作用插桩"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        self.log.append(time.monotonic())
        return Result(content="rw-ok", is_error=False)


class BlockingTool:
    """阻塞工具：执行中可被取消。"""

    read_only = False

    def name(self) -> str:
        return "block"

    def description(self) -> str:
        return "阻塞插桩"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(10)
        return Result(content="done", is_error=False)


@pytest.mark.asyncio
async def test_plain_text_turn_stops_immediately(tmp_path):
    """AC2：纯文本回合一轮即停。"""
    provider = FakeProvider([[StreamEvent(text="纯文本答复"), StreamEvent(done=True)]])
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("hi")
    events = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        events.append(ev)
    assert events[-1].done
    assert provider.call_count == 1
    assert conv.messages()[-1].content == "纯文本答复"


@pytest.mark.asyncio
async def test_multi_turn_loop(tmp_path):
    """AC1：读文件 → 回灌 → 续答，两轮迭代自动连环。"""
    p = tmp_path / "a.txt"
    p.write_text("hello\n", encoding="utf-8")
    read_call = ToolCall(id="c1", name="read_file", input=json.dumps({"path": str(p)}))
    scripts = [
        [StreamEvent(text="先读。"), StreamEvent(tool_calls=[read_call]), StreamEvent(done=True)],
        [StreamEvent(text="文件已读，最终答复。"), StreamEvent(done=True)],
    ]
    provider = FakeProvider(scripts)
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("读文件")
    events = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        events.append(ev)
    iters = [e.iter for e in events if e.iter > 0]
    assert iters == [1, 2]  # 两轮迭代
    starts = [e for e in events if e.tool and e.tool.phase == Phase.START]
    assert len(starts) == 1 and starts[0].tool.name == "read_file"
    assert any(e.text == "文件已读，最终答复。" for e in events)
    assert events[-1].done
    roles = [m.role for m in conv.messages()]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert conv.messages()[-1].content == "文件已读，最终答复。"


@pytest.mark.asyncio
async def test_max_iterations_stops(tmp_path):
    """AC3：模型反复调工具，达到 MAX_ITERATIONS 即停并提示。"""
    tool_call = ToolCall(id="c1", name="read_file", input='{"path": "x"}')
    provider = FakeProvider([[StreamEvent(tool_calls=[tool_call]), StreamEvent(done=True)]])
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("任务")
    events = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        events.append(ev)
    assert provider.call_count == MAX_ITERATIONS  # 恰好上限次请求
    assert any(e.notice == NOTICE_MAX_ITER for e in events)
    assert conv.last_role() == "assistant"


@pytest.mark.asyncio
async def test_unknown_tool_stops_after_max_unknown_run(tmp_path):
    """AC4：连续整轮未知工具达阈值即停（bypass 绕过 Ask，聚焦未知工具计数）。"""
    tool_call = ToolCall(id="c1", name="no_such_tool", input="{}")
    provider = FakeProvider([[StreamEvent(tool_calls=[tool_call]), StreamEvent(done=True)]])
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("任务")
    events = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.BYPASS, asyncio.Event()
    ):
        events.append(ev)
    assert any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)
    assert conv.last_role() == "assistant"
    assert provider.call_count == MAX_UNKNOWN_RUN


@pytest.mark.asyncio
async def test_unknown_tool_counter_resets_with_known(tmp_path):
    """AC4：混入已注册工具则计数重置。"""
    unknown = ToolCall(id="u1", name="no_such_tool", input="{}")
    known = ToolCall(id="k1", name="read_file", input='{"path": "x"}')
    scripts = [
        [StreamEvent(tool_calls=[unknown]), StreamEvent(done=True)],  # 轮1 unknown=1
        [StreamEvent(tool_calls=[known]), StreamEvent(done=True)],  # 轮2 known → 重置=0
        [StreamEvent(tool_calls=[unknown]), StreamEvent(done=True)],  # 轮3 unknown=1
        [StreamEvent(tool_calls=[unknown]), StreamEvent(done=True)],  # 轮4 unknown=2
        [StreamEvent(tool_calls=[unknown]), StreamEvent(done=True)],  # 轮5 unknown=3 → 停
    ]
    provider = FakeProvider(scripts)
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("任务")
    events = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.BYPASS, asyncio.Event()
    ):
        events.append(ev)
    assert provider.call_count == 5  # 已知轮重置后仍需 3 轮连续 unknown
    assert any(e.notice == NOTICE_UNKNOWN_TOOLS for e in events)


@pytest.mark.asyncio
async def test_concurrent_batch_preserves_order(tmp_path):
    """AC8：两只读并发、有副作用随后串行；结果按调用序回灌。"""
    state: dict = {"current": 0, "max_peak": 0, "done_at": []}
    rw_log: list = []
    registry = Registry()
    registry.register(SleepReadTool(state))
    registry.register(SideEffectTool(rw_log))
    calls = [
        ToolCall(id="c0", name="ro", input="{}"),
        ToolCall(id="c1", name="ro", input="{}"),
        ToolCall(id="c2", name="rw", input="{}"),
    ]
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=calls), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("任务")
    events = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.BYPASS, asyncio.Event()
    ):
        events.append(ev)
    assert state["max_peak"] >= 2  # 两只读确实并发
    assert rw_log and rw_log[0] >= max(state["done_at"]) - 1e-6  # rw 在两只读完成后开始
    tool_msgs = [m for m in conv.messages() if m.role == "tool"]
    assert tool_msgs
    ids = [r.tool_call_id for r in tool_msgs[0].tool_results]
    assert ids == ["c0", "c1", "c2"]  # 结果顺序 == 模型调用序
    assert [r.content for r in tool_msgs[0].tool_results] == ["ro-ok", "ro-ok", "rw-ok"]


@pytest.mark.asyncio
async def test_batch_partial_completion_no_crash(tmp_path):
    """批内部分先完成（FIRST_COMPLETED 早返回）时，等全部完成、结果不丢。"""
    state: dict = {"current": 0, "max_peak": 0, "done_at": []}
    registry = Registry()
    registry.register(SleepReadTool(state, delay=0.05, name="ro1"))
    registry.register(SleepReadTool(state, delay=0.05, name="ro2"))
    registry.register(SleepReadTool(state, delay=0.3, name="ro3"))  # 慢工具：制造「部分完成」返回点
    calls = [
        ToolCall(id="c0", name="ro1", input="{}"),
        ToolCall(id="c1", name="ro2", input="{}"),
        ToolCall(id="c2", name="ro3", input="{}"),
    ]
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=calls), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("任务")
    async for _ in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        pass
    tool_msgs = [m for m in conv.messages() if m.role == "tool"]
    assert tool_msgs
    assert [r.tool_call_id for r in tool_msgs[0].tool_results] == ["c0", "c1", "c2"]
    assert all(r.content == "ro-ok" for r in tool_msgs[0].tool_results)  # 三个都执行成功
    assert state["done_at"][-1] >= state["done_at"][0]  # 慢工具最后完成


@pytest.mark.asyncio
async def test_cancel_keeps_history_valid(tmp_path):
    """AC9：工具执行中取消，历史配对合法、无悬空 tool_use，后续可继续。"""
    registry = Registry()
    registry.register(BlockingTool())
    call = ToolCall(id="b1", name="block", input="{}")
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="正常答复"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("任务")
    cancel = asyncio.Event()
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.BYPASS, cancel
    ):
        if ev.tool and ev.tool.phase == Phase.START:
            cancel.set()  # 工具执行中取消
    roles = [m.role for m in conv.messages()]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert conv.last_role() == "assistant"
    tool_msg = conv.messages()[2]
    assert tool_msg.tool_results[0].is_error
    assert NOTICE_CANCELLED in tool_msg.tool_results[0].content
    # 取消后同一 conv 再跑纯文本，角色交替未坏
    async for _ in Agent(
        FakeProvider([[StreamEvent(text="后续答复"), StreamEvent(done=True)]]),
        registry,
        engine=_engine(tmp_path),
    ).run(conv, Mode.DEFAULT, asyncio.Event()):
        pass
    assert conv.last_role() == "assistant"


class ErrProvider:
    """流式直接出错的 provider。"""

    name = "fake"
    model = "fake-model"

    async def stream(self, req: Request):
        yield StreamEvent(err=RuntimeError("网络中断"))


@pytest.mark.asyncio
async def test_stream_error_recovers(tmp_path):
    """AC5：provider 流出错时停止本轮、发 err、历史以 assistant 收尾、程序不退出。"""
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("hi")
    events = []
    async for ev in Agent(ErrProvider(), registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        events.append(ev)
    assert any(e.err is not None for e in events)
    assert conv.last_role() == "assistant"  # 收尾保证角色交替，下一轮可继续


@pytest.mark.asyncio
async def test_plan_mode_read_only_tools_and_reminder(tmp_path):
    """AC9/AC13：规划模式仅只读工具；系统提示稳定、环境段非空；首轮完整提醒、次轮精简；reminder 不入历史。"""
    read_call = ToolCall(id="c1", name="read_file", input='{"path": "x"}')
    scripts = [
        [StreamEvent(tool_calls=[read_call]), StreamEvent(done=True)],  # 轮1
        [StreamEvent(text="计划完成"), StreamEvent(done=True)],  # 轮2（自然完成）
    ]
    provider = FakeProvider(scripts)
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("/plan")
    async for _ in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.PLAN, asyncio.Event()
    ):
        pass
    assert provider.call_count == 2  # 两轮迭代
    r1, r2 = provider.requests[0], provider.requests[1]
    # 规划模式 tools 仅只读，保注册顺序
    assert [d.name for d in r1.tools] == ["read_file", "glob", "grep"]
    # 系统提示稳定非空 + 环境段非空
    assert r1.system.stable and r1.system.environment
    # 首轮完整提醒、次轮精简
    assert r1.reminder.startswith("<system-reminder>")
    assert "只读工具" in r1.reminder
    assert r2.reminder != r1.reminder and "<system-reminder>" in r2.reminder
    # reminder 不写入持久历史
    assert all("<system-reminder>" not in m.content for m in conv.messages())


@pytest.mark.asyncio
async def test_normal_mode_full_tools_and_stable_same_as_plan(tmp_path):
    """普通模式 tools 全量；stable 系统提示与规划模式一致（规划提醒已移出系统通道）。"""
    scripts = [[StreamEvent(text="好"), StreamEvent(done=True)]]
    provider = FakeProvider(scripts)
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("hi")
    async for _ in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        pass
    r = provider.requests[0]
    assert [d.name for d in r.tools] == [
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "glob",
        "grep",
    ]
    assert r.reminder == ""  # 普通模式不注入 reminder
    assert r.system.stable  # 稳定系统提示非空


@pytest.mark.asyncio
async def test_cache_usage_passthrough(tmp_path):
    """AC6：缓存用量透传——llm.Usage(cache_write/cache_read) → Event.usage 携带。"""

    class CacheProvider:
        name = "fake"
        model = "fake-model"

        async def stream(self, req: Request):
            yield StreamEvent(
                usage=LLMUsage(input_tokens=10, output_tokens=5, cache_write=100, cache_read=50)
            )
            yield StreamEvent(text="好")
            yield StreamEvent(done=True)

    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("hi")
    usages = []
    async for ev in Agent(CacheProvider(), registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.usage is not None:
            usages.append(ev.usage)
    assert usages
    assert usages[0].cache_write == 100
    assert usages[0].cache_read == 50


# ─────────────────────────── ch06 权限集成 ───────────────────────────


def _write_call(path: str = "x.txt") -> ToolCall:
    return ToolCall(id="c1", name="write_file", input=json.dumps({"path": path, "content": "hi"}))


@pytest.mark.asyncio
async def test_blacklist_deny_in_bypass_feed_back(tmp_path):
    """AC1/N1：bypass 下 bash rm -rf / 仍被黑名单拦、回灌被拒原因、Loop 继续（agent 集成）。"""
    dangerous = ToolCall(id="c1", name="bash", input=json.dumps({"command": "rm -rf /"}))
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[dangerous]), StreamEvent(done=True)],
            [StreamEvent(text="已改为安全命令"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("任务")
    async for _ in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.BYPASS, asyncio.Event()
    ):
        pass
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert tool_msg.tool_results[0].is_error
    assert "黑名单" in tool_msg.tool_results[0].content
    assert provider.call_count == 2  # 拒绝回灌后 Loop 继续到次轮


@pytest.mark.asyncio
async def test_deny_feed_back_loop_continues(tmp_path):
    """AC11：沙箱被拒 → 错误结果回灌、Agent Loop 继续到次轮。"""
    denied = ToolCall(id="c1", name="read_file", input=json.dumps({"path": "/etc/passwd"}))
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[denied]), StreamEvent(done=True)],
            [StreamEvent(text="改走项目内路径"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("任务")
    events = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        events.append(ev)
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert tool_msg.tool_results[0].is_error
    assert "项目目录之外" in tool_msg.tool_results[0].content
    assert provider.call_count == 2  # 次轮继续
    assert events[-1].done


@pytest.mark.asyncio
async def test_deny_and_allow_batch_order_preserved(tmp_path):
    """AC11/N3：单批「被拒 + 放行」结果按原调用序、各自调用 ID 配对回灌。"""
    denied = ToolCall(id="c0", name="read_file", input=json.dumps({"path": "/etc/passwd"}))
    allowed = ToolCall(
        id="c1", name="read_file", input=json.dumps({"path": str(tmp_path / "a.txt")})
    )
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[denied, allowed]), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("任务")
    async for _ in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        pass
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    ids = [r.tool_call_id for r in tool_msg.tool_results]
    assert ids == ["c0", "c1"]  # 保序
    assert tool_msg.tool_results[0].is_error  # c0 被拒
    assert not tool_msg.tool_results[1].is_error  # c1 正常
    assert "hello" in tool_msg.tool_results[1].content


@pytest.mark.asyncio
async def test_ask_approval_allow_once(tmp_path):
    """AC10：default 写文件 Ask → 收 approval 事件 → ALLOW_ONCE → 执行。"""
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_calls=[_write_call(str(tmp_path / "x.txt"))]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="写完了"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("写文件")
    approvals = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            approvals.append(ev.approval)
            ev.approval.respond.set_result(Outcome.ALLOW_ONCE)
    assert len(approvals) == 1
    assert (tmp_path / "x.txt").exists()  # 文件被写
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert not tool_msg.tool_results[0].is_error


@pytest.mark.asyncio
async def test_ask_approval_deny_once(tmp_path):
    """AC10：DENY_ONCE → 文件未写、回灌被拒结果、Loop 继续。"""
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[_write_call()]), StreamEvent(done=True)],
            [StreamEvent(text="好，不写了"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("写文件")
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.DENY_ONCE)
    assert not (tmp_path / "x.txt").exists()
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert tool_msg.tool_results[0].is_error
    assert provider.call_count == 2  # Loop 继续


@pytest.mark.asyncio
async def test_ask_approval_forever_persists(tmp_path):
    """AC10/F8：ALLOW_FOREVER → 执行 + 精确规则写入本地层，重载后同调用放行。"""
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_calls=[_write_call(str(tmp_path / "x.txt"))]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="写完了"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("写文件")
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            ev.approval.respond.set_result(Outcome.ALLOW_FOREVER)
    assert (tmp_path / "x.txt").exists()
    text = (tmp_path / ".novacode" / "settings.local.yaml").read_text(encoding="utf-8")
    assert "Write(x.txt)" in text
    # 重载新引擎 → 同调用判放行
    engine2, _ = new_engine(str(tmp_path))
    d, _ = engine2.check(Mode.DEFAULT, _write_call(str(tmp_path / "x.txt")), read_only=False)
    assert d == Decision.ALLOW


@pytest.mark.asyncio
async def test_read_only_concurrent_no_approval(tmp_path):
    """AC13：一批只读不产生任何 approval 事件、仍并发执行（权限不退化）。"""
    state: dict = {"current": 0, "max_peak": 0, "done_at": []}
    registry = Registry()
    registry.register(SleepReadTool(state, delay=0.05, name="ro1"))
    registry.register(SleepReadTool(state, delay=0.05, name="ro2"))
    calls = [
        ToolCall(id="c0", name="ro1", input="{}"),
        ToolCall(id="c1", name="ro2", input="{}"),
    ]
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=calls), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("任务")
    approvals = []
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        if ev.approval is not None:
            approvals.append(ev.approval)
    assert not approvals  # 只读永不 Ask
    assert state["max_peak"] >= 2  # 并发不退化


@pytest.mark.asyncio
async def test_read_only_denied_by_sandbox_others_run(tmp_path):
    """AC13：被沙箱拦的只读得 errResult，其余只读照常完成。"""
    denied = ToolCall(id="c0", name="read_file", input=json.dumps({"path": "/etc/passwd"}))
    ok = ToolCall(id="c1", name="read_file", input=json.dumps({"path": str(tmp_path / "a.txt")}))
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[denied, ok]), StreamEvent(done=True)],
            [StreamEvent(text="完成"), StreamEvent(done=True)],
        ]
    )
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("任务")
    async for _ in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        pass
    tool_msg = next(m for m in conv.messages() if m.role == "tool")
    assert tool_msg.tool_results[0].is_error  # c0 被拒
    assert not tool_msg.tool_results[1].is_error  # c1 正常
    assert "hello" in tool_msg.tool_results[1].content


@pytest.mark.asyncio
async def test_cancel_during_approval(tmp_path):
    """AC12/N4：待批准等待中 cancel.set() → 干净收尾、历史合法、无挂起 task。"""
    provider = FakeProvider([[StreamEvent(tool_calls=[_write_call()]), StreamEvent(done=True)]])
    registry = new_default_registry()
    conv = Conversation()
    conv.add_user("写文件")
    cancel = asyncio.Event()
    before = set(asyncio.all_tasks())
    async for ev in Agent(provider, registry, engine=_engine(tmp_path)).run(
        conv, Mode.DEFAULT, cancel
    ):
        if ev.approval is not None:
            cancel.set()  # 待批准态取消
    roles = [m.role for m in conv.messages()]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert conv.last_role() == "assistant"
    tool_msg = conv.messages()[2]
    assert tool_msg.tool_results[0].is_error
    assert NOTICE_CANCELLED in tool_msg.tool_results[0].content
    leftover = set(asyncio.all_tasks()) - before
    assert not leftover  # 无挂起 task


# ─────────────────────────── ch08 紧急压缩 ───────────────────────────


class PTLFirstProvider:
    """第一次主对话请求抛 PTL；摘要请求返回摘要；重试返回正常文本。"""

    name = "fake"
    model = "fake-model"

    def __init__(self, retry_ptl: bool = False) -> None:
        self.retry_ptl = retry_ptl
        self.stream_calls = 0
        self.summarize_calls = 0

    async def stream(self, req: Request):
        self.stream_calls += 1
        if not req.tools:  # 摘要请求（tools 为空）
            self.summarize_calls += 1
            yield StreamEvent(text="<summary>## 1 主要请求和意图\nx</summary>")
            yield StreamEvent(done=True)
            return
        if self.stream_calls == 1 or (self.retry_ptl and self.stream_calls >= 3):
            yield StreamEvent(err=PromptTooLongError("too long"))
            return
        yield StreamEvent(text="重试成功")
        yield StreamEvent(done=True)


async def _run_all(agent, conv, mode=Mode.DEFAULT):
    events = []
    async for ev in agent.run(conv, mode, asyncio.Event()):
        events.append(ev)
    return events


async def test_agent_emergency_compact_succeeds(tmp_path):
    """F25/F26：撞墙 → 紧急压缩 → 重试成功，整体流程继续。"""
    provider = PTLFirstProvider()
    agent = Agent(provider, new_default_registry(), "test", _engine(tmp_path))
    conv = Conversation()
    conv.add_user("hi")
    events = await _run_all(agent, conv)
    assert events[-1].done
    phases = [e.compact.phase for e in events if e.compact is not None]
    assert CompactPhase.BEFORE_EMERGENCY in phases
    assert CompactPhase.AFTER_EMERGENCY in phases
    assert provider.summarize_calls == 1
    assert conv.messages()[-1].role == "assistant"  # 重试成功收尾


async def test_agent_emergency_compact_re_raise_on_second_ptl(tmp_path):
    """F26：紧急压缩后重试再次 PTL → 上抛错误，不再二次紧急压缩。"""
    provider = PTLFirstProvider(retry_ptl=True)
    agent = Agent(provider, new_default_registry(), "test", _engine(tmp_path))
    conv = Conversation()
    conv.add_user("hi")
    events = await _run_all(agent, conv)
    assert any(e.err is not None for e in events)
    assert all(not e.done for e in events)
    assert provider.summarize_calls == 1  # 只做一次紧急压缩


# ── ch12 Hook 接入 ──────────────────────────────────────────────────────────────


def _hook_py(cmd_body: str) -> str:
    """用当前解释器执行一段 python，构造跨平台 shell 命令串。"""
    return f'"{sys.executable}" -c "{cmd_body}"'


def _hook_exit2(stderr_text: str) -> str:
    return _hook_py(f"import sys; print('{stderr_text}', file=sys.stderr); sys.exit(2)")


def _make_hook_rule(**kw):
    from novacode.hook import Event as HookEvent
    from novacode.hook.rule import Action, ActionType, Rule, ShellAction

    kw.setdefault("event", HookEvent.PRE_TOOL_USE)
    return Rule(
        name=kw["name"],
        event=kw["event"],
        action=Action(type=ActionType.SHELL, shell=ShellAction(command=kw["command"])),
    )


async def test_hook_pre_tool_use_blocked(tmp_path):
    """PreToolUse 拦截：tool_result 为 [hook <name>] <reason>，START/END 仍 emit，文件未写（F32）。"""
    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine

    hook_engine = HookEngine(
        [
            _make_hook_rule(
                name="block-write",
                event=HookEvent.PRE_TOOL_USE,
                command=_hook_exit2("blocked-by-hook"),
            )
        ],
        ["test.yaml"],
    )
    write_call = ToolCall(
        id="c1",
        name="write_file",
        input=json.dumps({"path": str(tmp_path / "x.txt"), "content": "hi"}),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[write_call]), StreamEvent(done=True)],
            [StreamEvent(text="好的，已处理。"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(
        provider, new_default_registry(), "test", _engine(tmp_path), hook_engine=hook_engine
    )
    conv = Conversation()
    conv.add_user("写文件")
    events = await _run_all(agent, conv)
    assert not (tmp_path / "x.txt").exists()  # 文件未被写入
    ends = [e for e in events if e.tool and e.tool.phase == Phase.END]
    assert ends and "[hook block-write] blocked-by-hook" in ends[0].tool.result
    starts = [e for e in events if e.tool and e.tool.phase == Phase.START]
    assert len(starts) == 1 and starts[0].tool.name == "write_file"
    assert events[-1].done  # LLM 看到拦截结果后正常收尾


async def test_hook_pre_user_message_injects_prompt(tmp_path):
    """PreUserMessage 注入的 prompt 在下一轮请求 reminder 中可见（F33：下一次 _request 拼接）。"""
    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine
    from novacode.hook.rule import Action, ActionType, PromptAction, Rule

    hook_engine = HookEngine(
        [
            Rule(
                name="zh-cn",
                event=HookEvent.PRE_USER_MESSAGE,
                action=Action(type=ActionType.PROMPT, prompt=PromptAction(text="用 zh-CN 回复")),
            )
        ],
        ["test.yaml"],
    )
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    read_call = ToolCall(id="c1", name="read_file", input=json.dumps({"path": str(p)}))
    provider = FakeProvider(
        [
            [
                StreamEvent(text="先读。"),
                StreamEvent(tool_calls=[read_call]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="最终答复。"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(
        provider, new_default_registry(), "test", _engine(tmp_path), hook_engine=hook_engine
    )
    conv = Conversation()
    conv.add_user("hi")
    events = await _run_all(agent, conv)
    assert events[-1].done
    assert len(provider.requests) == 2
    # 第一轮注入（发生在请求发出后）；第二轮拼接进 reminder
    assert "用 zh-CN 回复" not in provider.requests[0].reminder
    assert "用 zh-CN 回复" in provider.requests[1].reminder


async def test_hook_reminder_combines_with_plan_reminder(tmp_path):
    """plan 模式下同一轮 reminder 串同时含 plan reminder 与 hook 注入（N4/F33 协同）。"""
    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine
    from novacode.hook.rule import Action, ActionType, PromptAction, Rule

    hook_engine = HookEngine(
        [
            Rule(
                name="zh-cn",
                event=HookEvent.PRE_USER_MESSAGE,
                action=Action(type=ActionType.PROMPT, prompt=PromptAction(text="用 zh-CN 回复")),
            )
        ],
        ["t.yaml"],
    )
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    read_call = ToolCall(id="c1", name="read_file", input=json.dumps({"path": str(p)}))
    provider = FakeProvider(
        [
            [
                StreamEvent(text="先读。"),
                StreamEvent(tool_calls=[read_call]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="计划完成。"), StreamEvent(done=True)],
        ]
    )
    agent = Agent(
        provider, new_default_registry(), "test", _engine(tmp_path), hook_engine=hook_engine
    )
    conv = Conversation()
    conv.add_user("读文件")
    await _run_all(agent, conv, mode=Mode.PLAN)
    assert len(provider.requests) == 2
    # 第一轮注入的 hook prompt 在第二轮拼接进 reminder；plan 模式下 plan reminder 同时存在
    assert "用 zh-CN 回复" in provider.requests[1].reminder
    assert "计划" in provider.requests[1].reminder


async def test_hook_stop_emitted_before_done(tmp_path):
    """Stop hook 在 Done 事件前一刻被 emit（自然完成路径）。"""
    from novacode.hook import Event as HookEvent
    from novacode.hook.engine import Engine as HookEngine

    marker = tmp_path / "stop-marker"
    hook_engine = HookEngine(
        [
            _make_hook_rule(
                name="notify-stop",
                event=HookEvent.STOP,
                command=_hook_py(f"open(r'{marker}','w').write('x')"),
            )
        ],
        ["test.yaml"],
    )
    provider = FakeProvider([[StreamEvent(text="完成"), StreamEvent(done=True)]])
    agent = Agent(
        provider, new_default_registry(), "test", _engine(tmp_path), hook_engine=hook_engine
    )
    conv = Conversation()
    conv.add_user("hi")
    events = await _run_all(agent, conv)
    assert events[-1].done
    for _ in range(40):
        if marker.exists():
            break
        await asyncio.sleep(0.05)
    assert marker.exists()  # Stop hook 触发了
