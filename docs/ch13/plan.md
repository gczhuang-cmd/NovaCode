# SubAgent 机制 Plan

> 修订记录（2026-08-11，对照实际代码）：
> - **TUI 架构**：删除 Textual 全部引用（`novaCodeApp`/`app.py`/`stream.py`/`on_mount`/`BINDINGS`/`SessionState`）。实际 TUI 是 **prompt_toolkit 非全屏 + Rich**，主循环在 `tui/run.py::run()`；task-notification 注入改为「主循环每轮排空 done 队列」，不用后台协程。
> - **Tool 协议无 context**：`tool.Tool.execute(args: str) -> Result`，无 `ToolContext`。Agent 工具自己 `json.loads(args)`；嵌套阻断统一走工具过滤；`is_fork_context` 只作辅助。
> - **类型命名**：`Mode`/`Outcome`/`Decision`/`parse_mode`/`ApprovalRequest`（`agent/__init__.py`）。
> - **文件结构**：`agent/__init__.py`（非 agent.py）；Skill fork 在 `skills/executor.py`（非 `tui/skill_fork.py`）；`launch_fork` 放 `agent/launch.py`。
> - **`SessionRuntime` 是 dataclass**，构造需完整字段；`new_session_context` 从 `novacode.compact` 导入。
> - **`ALL_AGENT_DISALLOWED_TOOLS` 含 5 个元工具**；task 工具 `is_system=False`。
> - **子 Agent Ask 不弹窗**：`approval_upgrader` 默认实现返回「拒绝本次」。**ESC 切后台不做**。**model 不切 provider**。

## 技术栈

- 语言：Python 3.12+
- TUI：**prompt_toolkit 非全屏输入框 + Rich 直写终端 scrollback**（继承 ch02 起的现状，无 Textual）
- 配置：YAML 解析（`pyyaml`，import 名 `yaml`）
- LLM 通信：协议无关 `novacode.llm.Provider`（`new_provider(cfg)`，anthropic/openai 适配器）
- 并发模型：asyncio + `asyncio.create_task` / `asyncio.Queue` / `asyncio.CancelledError`
- 工具调用与权限引擎沿用 ch04~ch12 既有的 `novacode.agent` / `novacode.permission` / `novacode.hook` 模块

## 架构概览

本章实现拆为四个层次：

1. **subagent 包**（新增，核心数据层）——定义 Agent 角色的数据结构、Markdown+YAML 解析、Catalog 多来源加载、内置角色随包发布
2. **task 包**（新增，后台运行层）——`task.Manager` 管理后台任务生命周期，4 个内置工具（TaskList / TaskGet / TaskStop / SendMessage）
3. **agent 包扩展**——新增 `run_to_completion` 方法、5 个新构造参数、Fork 路径辅助函数 `build_forked_messages`、`ApprovalUpgrader` 类型、`launch_fork` 公共启动函数
4. **工具与 TUI 集成层**——Agent 工具实现、工具过滤多层防线常量、TUI 接入 task notification、Skill fork 改造为复用 SubAgent 底座

模块构成：

- `subagent.Definition` / `subagent.Catalog` / `subagent.Source*` — 数据结构与三层加载
- `novacode.subagent.builtin/*.md` — 内置 3 个角色文件，`importlib.resources` 读取
- `task.Manager` / `task.BackgroundTask` — 后台任务管理与生命周期
- `task.TaskListTool` / `TaskGetTool` / `TaskStopTool` / `SendMessageTool` — 4 个内置工具，注册到 `tool.Registry`
- `agent.Agent.run_to_completion` / `system_prompt` / `max_turns` / `permission_mode` / `dont_ask` / `approval_upgrader` — Agent 类扩展
- `agent/fork.py` — `build_forked_messages`、`is_fork_context`、Fork Boilerplate 常量
- `agent/agent_tool.py` — Agent 工具实现
- `agent/launch.py` — `launch_fork`（Skill fork 与 Agent 工具 Fork 共用）
- `tool/filter.py` — `ALL_AGENT_DISALLOWED_TOOLS` / `ASYNC_AGENT_ALLOWED_TOOLS` 常量与过滤函数
- `tui/run.py` 改动 — task_mgr / subagent_catalog 参数、`set_parent` 回填、每轮注入 task notification
- `tui/tasks.py` — `build_task_notification` / `_inject_task_notifications`
- `skills/executor.py` 改造 — `_run_subagent` 复用 `agent.launch_fork`

## 核心数据结构

### subagent.Definition

```python
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from novacode.permission import Mode

class Source(IntEnum):
    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3  # 占位

    def __str__(self) -> str:
        return {0: "builtin", 1: "user", 2: "project", 3: "plugin"}.get(int(self), "unknown")

@dataclass
class Definition:
    """一个 Agent 角色的完整定义，从 Markdown+YAML frontmatter 解析。"""

    name: str                              # frontmatter.name（-> agent_type）
    description: str                       # frontmatter.description（-> when_to_use）
    tools: list[str] = field(default_factory=list)             # frontmatter.tools 白名单;空表示不收窄
    disallowed_tools: list[str] = field(default_factory=list)  # frontmatter.disallowedTools 黑名单
    model: Literal["haiku", "sonnet", "opus", "inherit"] = "inherit"  # 本期解析但不切 provider
    max_turns: int = 0                     # 0 表示沿用全局默认（MAX_ITERATIONS=25）
    permission_mode: Mode = Mode.DEFAULT   # 默认模式;"dontAsk" 由 dont_ask 字段单独表达
    dont_ask: bool = False                 # 是否启用"绕过 Ask"的子 Agent 兜底模式
    background: bool = False               # 强制后台
    system_prompt: str = ""                # Markdown body（去 frontmatter 后的全文）
    file_path: str = ""                    # 定义文件绝对路径（用于调试）
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        return self.name == "__fork__"
```

### subagent.Catalog

```python
import threading

class Catalog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._defs: dict[str, Definition] = {}               # name -> 最高优先级定义
        self._by_source: dict[Source, list[Definition]] = {} # 各层副本（供 /agents 类展示与 debug）

    def resolve(self, name: str) -> Definition | None: ...
    def list(self) -> list[Definition]: ...                  # 按 name 排序
    def list_by_source(self, src: Source) -> list[Definition]: ...

    def fork_definition(self) -> Definition:
        """返回 Fork 路径用的临时 Definition——name="__fork__"，system_prompt 留空（继承），
        tools/disallowed_tools 留空（工具集由父 + 过滤规则决定）。"""
        return Definition(
            name="__fork__",
            description="Fork-based subagent",
            model="inherit",
            permission_mode=Mode.DEFAULT,
        )

def load_catalog(root: str) -> Catalog:
    """顺序加载：builtin -> user -> project，优先级高的覆盖低的；
    解析错误走 stderr 警告并跳过；返回非 None Catalog 即使无任何定义。"""
```

### task.Manager 与 BackgroundTask

```python
import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum

class Status(IntEnum):
    RUNNING = 0
    COMPLETED = 1
    FAILED = 2
    CANCELLED = 3

@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

@dataclass
class BackgroundTask:
    """一个后台子 Agent 的完整状态快照。"""

    id: str                                # manager 生成，如 "task_<8 字节十六进制>"
    name: str                              # F1 中 Agent 工具 name 参数，可空
    sub_agent: "Agent"
    conv: "Conversation"
    task: str                              # 初始任务文本（send_message 不更新此字段）
    status: Status = Status.RUNNING
    result: str = ""                       # 跑完的最终文本
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    handle: asyncio.Task | None = None     # 跑动协程的 asyncio.Task，Stop 时调 cancel()
    usage: Usage = field(default_factory=Usage)
    tool_count: int = 0
    last_activity: str = ""

@dataclass
class PartialState:
    """前台→后台移交时已收集的中间状态。"""

    tool_count: int = 0
    last_activity: str = ""
    usage: Usage = field(default_factory=Usage)

class Manager:
    """管理后台任务。协程安全（单事件循环）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}              # name -> id，后启动的覆盖
        self._done: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._counter: int = 0

    async def launch(self, ag: "Agent", conv: "Conversation",
                     name: str, task: str) -> str:
        """注册 BackgroundTask（RUNNING）并 create_task 跑 run_to_completion。
        协程包 try/except BaseException：CancelledError -> CANCELLED（re-raise），
        其它异常 -> FAILED + err；finally 写 end_time、cancel 聚合器、push task_id 到 _done。"""
        ...
    async def adopt_running(self, ag: "Agent", conv: "Conversation",
                            name: str, events: asyncio.Queue,
                            handle: asyncio.Task, partial: PartialState) -> str:
        """前台超时切后台：不重新调 run_to_completion（已在前台启动），只注册
        BackgroundTask 状态、聚合 events、等队列收尾后写终态 + push done。"""
        ...
    def get(self, id: str) -> BackgroundTask | None: ...
    def list(self) -> list[BackgroundTask]: ...          # 按 start_time 升序
    async def stop(self, id: str) -> bool: ...           # bt.handle.cancel()
    def subscribe_done(self) -> asyncio.Queue[str]: ...
    async def send_message(self, name: str, message: str) -> str:
        """仅 status==COMPLETED 允许；否则 raise TaskBusy。把 message 追加到 conv，
        重新 create_task 跑 run_to_completion（同 id，status 重置 RUNNING）。"""
        ...
    async def upgrade_approval(self, req) -> tuple["Outcome", bool]:
        """本期默认实现：返回 (Outcome.DENY_ONCE, True)——子 Agent Ask 一律拒绝本次。
        （不做主 TUI 弹窗，见 spec 修订记录）"""
        ...
```

### agent 包扩展

```python
# novacode/agent/permission_upgrade.py
from typing import Awaitable, Callable
from novacode.permission import Outcome
from novacode.agent import ApprovalRequest   # 定义在 agent/__init__.py

ApprovalUpgrader = Callable[
    [ApprovalRequest],
    Awaitable[tuple[Outcome, bool]],
]

async def default_upgrader(req: ApprovalRequest) -> tuple[Outcome, bool]:
    """默认实现：拒绝本次。子 Agent 的 Ask 工具被拒，原因回灌让它调整。"""
    return Outcome.DENY_ONCE, True
```

```python
# Agent.__init__ 新增关键字参数（追加在现有 `*` 关键字参数之后）
class Agent:
    system_prompt: str | None = None        # 非空时覆盖默认系统提示（run 内 sys_text = 它 or build_system_prompt）
    max_turns: int = 0                      # 0 表示用全局 MAX_ITERATIONS=25（run 循环上限）
    permission_mode: Mode | None = None     # None 时 run_to_completion 用 Mode.DEFAULT
    dont_ask: bool = False                  # True 时 _execute_batched 串行分支 Ask 短路为 Allow
    approval_upgrader: ApprovalUpgrader | None = None  # Ask 升级回调; None = 默认拒绝

    async def run_to_completion(
        self,
        conv: "Conversation",
        task: str,
        events: asyncio.Queue | None = None,
    ) -> str:
        """执行子 Agent 的"跑到底"循环。

        **复用主 ``run`` 的同一段循环**：`async for ev in self.run(conv, mode, cancel)`
        收集事件流，把 tool / usage 事件转发到外部 events 队列，最终返回
        conv 末尾 assistant 文本。区别：
        - mode = self.permission_mode or Mode.DEFAULT
        - 不传 memory_manager 时自动跳过记忆更新（run 内 _maybe_trigger_memory_update 已判 None）
        - 触达 max_turns（收到 NOTICE_MAX_ITER）抛 MaxTurnsReached
        """
        ...
```

`Agent.__init__` 新增关键字参数（现有签名 `Agent(provider, registry, version, engine, *, runtime, memory_manager, instruction_text, memory_text, catalog, allowed_tools, hook_engine)`）：
- `system_prompt: str | None` — 非空时 `run` 装配 `sys_text` 用它覆盖 `build_system_prompt`
- `max_turns: int` — `run` 的 `for it in range(1, self.max_turns or MAX_ITERATIONS + 1)`
- `permission_mode: Mode | None` — `run_to_completion` 选 mode
- `dont_ask: bool` — `_execute_batched` 串行分支 Ask 短路
- `approval_upgrader: ApprovalUpgrader | None` — Ask 升级回调

provider 无需新增参数——子 Agent 构造时直接传父 provider。

### fork.py 内容

```python
FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

# Fork 子 Agent 首条 user 消息的前缀，约束其行为。
FORK_BOILERPLATE = """<fork_boilerplate>
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则(不可协商):
1. 不能再 Fork(调用 Agent 工具会被拦截)。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具:读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告以 "Scope:" 开头,500 字以内。
</fork_boilerplate>
"""

def build_forked_messages(parent_msgs: list[Message]) -> list[Message]:
    """把父对话克隆到 Fork 子对话,处理悬空 tool_use,追加 Boilerplate。

    行为:
      1. 深拷贝 parent_msgs(所有 Message + 内部 tool_calls / tool_results 列表)
      2. 扫描末尾 assistant 消息的 tool_calls,如果对应的 tool_results 回合缺失,
         生成 placeholder tool_results(每个 ID 一条 "[forked, skipped]" 错误内容)
      3. 追加最后一条 user 消息 = FORK_BOILERPLATE

    任务文本不在此注入——由 run_to_completion 的 `if task: conv.add_user(task)`
    统一追加,保证不重复。
    """
    ...

def is_fork_context(msgs: list[Message]) -> bool:
    """判定消息历史是否来自 Fork(扫描 <fork_boilerplate> 标签)。

    本期不作运行时拦截闸(嵌套阻断统一走 ALL_AGENT_DISALLOWED_TOOLS 工具过滤),
    保留为 build_forked_messages 相关测试与 debug 辅助。
    """
    ...
```

### Agent 工具

`src/novacode/agent/agent_tool.py`:

```python
@dataclass
class AgentArgs:
    prompt: str
    description: str
    subagent_type: str = ""
    model: str = ""
    run_in_background: bool = False
    name: str = ""

class AgentTool:
    """注册到 ``tool.Registry`` 的统一 Agent 工具。"""

    read_only = False
    is_system = False          # 普通工具（子 Agent 构造时经过滤剔除）

    def __init__(
        self,
        catalog: subagent.Catalog,
        task_mgr: task.Manager,
        parent: Agent | None,          # 取 provider / registry / engine / runtime 等;cli 先 None,run 内 set_parent
        bg_enabled: bool,              # N6 配置开关
    ) -> None:
        self._parent: Agent | None = parent
        ...

    def set_parent(self, ag: Agent) -> None:
        """tui.run 构造主 Agent 后回填 parent 引用。"""
        self._parent = ag

    def name(self) -> str: return "Agent"
    def description(self) -> str:
        """基础描述 + subagent_type 可选值（catalog.list() 渲染）。"""
        ...
    def parameters(self) -> dict:
        """按 spec F1 写 JSON Schema：prompt/description/subagent_type/model/run_in_background/name。"""
        ...
    async def execute(self, args: str) -> Result:
        """Tool 协议：args 是 raw JSON 字符串，自己 json.loads。主流程见下。"""
        ...
```

**execute 主流程**（无 ToolContext，嵌套阻断靠构造期工具过滤）：

```python
async def execute(self, args: str) -> Result:
    data = json.loads(args or "{}")
    a_args = AgentArgs(**data) if isinstance(data, dict) else AgentArgs()
    if not a_args.prompt:
        return Result(is_error=True, content="prompt is required")
    if not a_args.description:
        return Result(is_error=True, content="description is required")
    parent = self._parent
    if parent is None:
        return Result(is_error=True, content="Agent tool not wired to parent")

    # resolve 定义
    if a_args.subagent_type:
        defi = self._catalog.resolve(a_args.subagent_type)
        if defi is None:
            return Result(is_error=True,
                          content=f"unknown subagent_type: {a_args.subagent_type}")
    else:
        defi = self._catalog.fork_definition()

    # 决定后台
    background = defi.background or a_args.run_in_background or defi.is_fork()
    if background and not self._bg_enabled:
        return Result(is_error=True,
                      content="background mode is disabled by config (enableSubAgentBackground=false)")

    # 工具过滤多层防线（构造期唯一一次;主 Agent 工具列表不受影响）
    allowed = apply_agent_tool_filter(FilterParams(
        all=[t.name() for t in self._parent._registry.definitions()]  # 或 registry 遍历
            if False else registry_names(self._parent),
        source=int(defi.source),
        background=background,
        allowed=defi.tools,
        disallowed=defi.disallowed_tools,
    ))

    # 构造子 Agent（共享父 provider / registry / engine / hook_engine;独立 runtime）
    sub_runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(tempfile.gettempdir()),
        context_window=parent.runtime.context_window,  # 继承主窗口阈值
    )
    sub_agent = Agent(
        parent._provider, parent._registry, parent._version, parent._engine,
        runtime=sub_runtime,
        allowed_tools=allowed,
        system_prompt=defi.system_prompt,
        max_turns=defi.max_turns,
        permission_mode=defi.permission_mode,
        dont_ask=defi.dont_ask,
        approval_upgrader=default_upgrader,          # 本期默认拒绝
        hook_engine=parent._hook_engine,
    )

    # 子 conv:定义式空白 / Fork 装填
    if defi.is_fork():
        forked = build_forked_messages(parent_conv_msgs(parent))
        sub_conv = Conversation.from_messages(forked)
    else:
        sub_conv = Conversation()
    # 注意:任务文本统一由 run_to_completion / launch 注入,此处不 add_user

    # 后台路径
    if background:
        task_id = await self._task_mgr.launch(sub_agent, sub_conv, a_args.name, a_args.prompt)
        return Result(content=json.dumps({"task_id": task_id, "status": "async_launched"}))

    # 前台路径（超时自动切后台）
    events: asyncio.Queue = asyncio.Queue(maxsize=64)
    partial = PartialState()
    aggregator = asyncio.create_task(self._task_mgr.aggregate(events, partial))
    try:
        final_text = await asyncio.wait_for(
            sub_agent.run_to_completion(sub_conv, a_args.prompt, events),
            timeout=AUTO_BACKGROUND_SECONDS,
        )
        return Result(content=final_text)
    except asyncio.TimeoutError:
        running = asyncio.create_task(
            sub_agent.run_to_completion(sub_conv, "", events))  # conv 已含 task,不重复 add
        task_id = await self._task_mgr.adopt_running(
            sub_agent, sub_conv, a_args.name, events, running, partial)
        return Result(content=json.dumps(
            {"task_id": task_id, "status": "timed_out_to_background"}))
    except Exception as e:
        return Result(is_error=True, content=f"subagent error: {e}")
    finally:
        aggregator.cancel()
        await events.put(None)  # 触发聚合器收尾
```

### 工具过滤多层防线

`src/novacode/tool/filter.py`:

```python
# 任何子 Agent 永远不能用的元工具名列表。
# 子 Agent 不能嵌套（Agent）、不能操控后台任务（TaskList/TaskGet/TaskStop/SendMessage）。
ALL_AGENT_DISALLOWED_TOOLS: list[str] = [
    "Agent", "TaskList", "TaskGet", "TaskStop", "SendMessage",
]

# 自定义（user / project / plugin 来源）Agent 比内置 Agent 多禁用的工具。本期为空。
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = []

# 后台 Agent 工具白名单。不含任何元工具。
ASYNC_AGENT_ALLOWED_TOOLS: list[str] = [
    "read_file", "write_file", "edit_file",
    "glob", "grep",
    "bash",
    "load_skill", "install_skill",
]
# MCP 工具按 mcp__ 前缀动态识别（is_mcp_or_skill）。

@dataclass
class FilterParams:
    all: list[str]                    # registry 的全部工具名（按注册顺序）
    source: int                       # subagent.Source 的整数值（0=builtin,1=user,2=project,3=plugin）
    background: bool
    allowed: list[str] = field(default_factory=list)     # Agent 定义 tools 白名单
    disallowed: list[str] = field(default_factory=list)  # Agent 定义 disallowedTools 黑名单

def apply_agent_tool_filter(p: FilterParams) -> list[str]:
    """按 spec F30 顺序过滤。返回最终 allowed 列表（传给 Agent(allowed_tools=...)）。"""
    ...

def is_mcp_or_skill(name: str) -> bool:
    """后台白名单的动态识别：mcp__ 前缀工具放行。"""
    return name.startswith("mcp__")
```

### TUI 集成层

`src/novacode/tui/run.py` 改动：
- `run()` 新增关键字参数 `task_mgr: task.Manager | None = None`、`subagent_catalog: subagent.Catalog | None = None`
- 构造主 `agent` 后回填 Agent 工具 parent：
  ```python
  at = registry.get("Agent")
  if at is not None and hasattr(at, "set_parent"):
      at.set_parent(agent)
  ```
- 主循环每轮（收到新输入、`holder.conv.add_user(text)` 之前）排空 done 队列注入通知：
  ```python
  if task_mgr is not None:
      await _inject_task_notifications(task_mgr, runtime)
  ```

`src/novacode/tui/tasks.py` 新增：
- `build_task_notification(bt) -> str`：渲染 `<task-notification>` 块（F19 格式）
- `async def _inject_task_notifications(task_mgr, runtime) -> None`：非阻塞排空 `subscribe_done()` 队列，对每个 id 取 `BackgroundTask`，构造通知，`runtime.append_reminders([notif])`。主 Agent 下一次 run 时 `take_reminders()` 自然注入 reminder 区。

`src/novacode/skills/executor.py` 改造：
- 删除 `_run_subagent` 内零散的子 Agent 构造逻辑
- 改为调 `agent.launch_fork(opts)`（函数内延迟导入避免循环）

## 模块设计

### 模块 A：`novacode.subagent`

**职责：**
- 数据结构 `Definition` / `Source`
- Markdown + YAML 解析（参照 `skills/parser.py` 的 `parse_frontmatter_and_body` 思路独立实现一份，字段不同）
- 三层 + 内置随包加载

**对外接口：**
- `load_catalog(root: str) -> Catalog`
- `Catalog.resolve(name)` / `list()` / `fork_definition()`

**依赖：**
- `novacode.permission`（解析 permission_mode 字段）
- `pyyaml`
- 标准库 `pathlib` / `importlib.resources`

**关键设计：**
- Markdown 解析在 `subagent/parser.py` 独立实现（不共享 skills parser，字段集不同，避免耦合）
- 内置文件 `novacode/subagent/builtin/*.md` 通过 `importlib.resources.files("novacode.subagent.builtin")` 读取
- 加载错误统一 stderr：`print(f"subagent {name}: ... skipped", file=sys.stderr)`；内置定义解析失败直接 raise（N4 fail-fast）

### 模块 B：`novacode.task`

**职责：**
- 后台任务生命周期管理
- 4 个内置工具（TaskList / TaskGet / TaskStop / SendMessage）

**对外接口：**
- `Manager()`
- `launch / adopt_running / get / list / stop / send_message / subscribe_done / upgrade_approval`
- `TaskListTool` / `TaskGetTool` / `TaskStopTool` / `SendMessageTool` 4 个 Tool 类

**依赖：**
- `novacode.agent`（Agent）
- `novacode.conversation`
- `novacode.tool`
- `novacode.llm`

**关键设计：**
- `_done` 队列 `maxsize=32`；真满则 `put_nowait` 抛 `QueueFull`，捕获后丢弃 + stderr 警告
- `launch` 协程包 `try/except BaseException`：CancelledError → `CANCELLED`（re-raise）；其它异常 → `FAILED` + `err`
- `stop` 调 `bt.handle.cancel()`，handle 是 `asyncio.create_task(run_to_completion(...))`
- `send_message`：仅 `status == COMPLETED` 允许；否则 raise `TaskBusy`。重新跑时用*同 id*，status 重置 RUNNING
- `aggregate(events, partial)`：消费 events 队列，聚合 tool_count / last_activity / usage 到 partial 或 bt

### 模块 C：`novacode.agent` 扩展

**职责：**
- 新增 `run_to_completion` 方法
- 新增 5 个 `__init__` 关键字参数
- Fork 路径辅助

**对外新增接口：**
- `Agent.run_to_completion(conv, task, events=None) -> str`
- `Agent.__init__(..., system_prompt, max_turns, permission_mode, dont_ask, approval_upgrader)`
- `build_forked_messages` / `is_fork_context`
- `ApprovalUpgrader` 类型 + `default_upgrader`
- `launch_fork(opts)`

**关键设计：**
- `run_to_completion` **不重复写循环**：`async for ev in self.run(conv, mode, cancel)` 复用主对话 ReAct 循环（含 hook 调度、上下文管理、权限判定全部复用）。`mode = self.permission_mode or Mode.DEFAULT`；把 `ev.tool` / `ev.usage` 转发到外部 `events` 队列；`ev.done` 后返回 conv 末尾 assistant 文本；收到 `NOTICE_MAX_ITER`（触达上限）抛 `MaxTurnsReached`
- `run` 循环上限改为 `self.max_turns or MAX_ITERATIONS`（一处小改，主 Agent 传 0 仍 25）
- `dont_ask` 短路点：`_execute_batched` 串行分支的 `if decision == Decision.ASK:` 之前：
  ```python
  if decision == Decision.ASK and self.dont_ask:
      decision = Decision.ALLOW   # 黑名单/沙箱/deny 规则已在前四层拦截;此处仅放行 Ask
  ```
- `approval_upgrader` 调用点：同一串行分支 ASK 判定处：
  ```python
  if decision == Decision.ASK and self.approval_upgrader is not None:
      req = ApprovalRequest(name=call.name, args=preview, reason=reason,
                            respond=asyncio.get_running_loop().create_future())
      outcome, ok = await self.approval_upgrader(req)
      if ok:
          decision = ...   # ALLOW_ONCE/ALLOW_FOREVER -> 继续执行;DENY_ONCE -> 拒绝回灌
      # ok=False -> 走默认 emit Approval 路径（主 Agent inline 场景保留）
  ```

**Fork Boilerplate 注入策略：**
- `build_forked_messages` 追加 user = `FORK_BOILERPLATE`（首条 user，约束行为）；任务文本由 `run_to_completion` 的 `if task: conv.add_user(task)` 统一追加
- `is_fork_context` 扫描历史 user 消息内容寻找 `<fork_boilerplate>`（辅助/测试用）

### 模块 D：Agent 工具与 TUI 集成

**职责：**
- 把 Agent 工具与 4 个 task 工具注册到 registry
- TUI 接入 task notification
- 改造 Skill fork

**对外接口：**
- `AgentTool(catalog, task_mgr, parent, bg_enabled)`
- `agent.launch_fork(opts)` 公共 Fork 启动函数（Skill fork 与 Agent 工具 Fork 都调）

**关键设计：**
- `AgentTool.execute` 前台 inline 返回 final_text；超时切后台返回 JSON `{"task_id":..., "status":"timed_out_to_background"}`
- 嵌套阻断靠**构造期工具过滤**（子 Agent 工具列表无 Agent / 元工具），`execute` 无需检测调用方
- `launch_fork` 构造 Fork 子 Agent：`permission_mode=Mode.BYPASS`（继承 skills fork 现状：allowed_tools 已收窄，黑名单/沙箱仍拦，无 UI 审批卡死风险）
- task notification 注入：主循环每轮 `_inject_task_notifications` 非阻塞排空 done 队列 → `runtime.append_reminders`

## 模块交互

### 启动期 wiring

```
cli.main()
  ├── tool.new_default_registry()    → registry
  ├── permission.new_engine(root)    → engine
  ├── SessionRuntime(...)            → runtime
  ├── skills.Catalog.load(...)       → catalog
  ├── hook.load(...)                 → hook_engine
  ├── subagent.load_catalog(root)    → subagent_catalog          ← 新增
  ├── task.Manager()                 → task_mgr                  ← 新增
  ├── registry.register(task.TaskListTool(task_mgr))             ← 新增
  ├── registry.register(task.TaskGetTool(task_mgr))              ← 新增
  ├── registry.register(task.TaskStopTool(task_mgr))             ← 新增
  ├── registry.register(task.SendMessageTool(task_mgr))          ← 新增
  ├── registry.register(AgentTool(subagent_catalog, task_mgr,
  │                            parent=None,
  │                            bg_enabled=cfg.effective_enable_subagent_background()))  ← 新增
  └── await tui.run(cfg, registry, engine, runtime, ..., catalog, hook_engine,
                    task_mgr=task_mgr, subagent_catalog=subagent_catalog)  ← 新增参数

tui.run 内:
  agent = Agent(provider, registry, version, engine, runtime=runtime, ..., hook_engine=hook_engine)
  at = registry.get("Agent"); at.set_parent(agent)   ← parent 回填（agent 在 run 内构造）
  主循环每轮输入前: await _inject_task_notifications(task_mgr, runtime)
```

### 运行时：主 Agent 调 Agent 工具（前台，定义式）

```
LLM 流式产出 tool_use:{name:"Agent", input:{prompt:"...", subagent_type:"Explore"}}
    ↓
Agent._execute_batched → registry.execute("Agent", args) → AgentTool.execute(args)
    ↓
AgentTool.execute:
    1. json.loads(args) -> AgentArgs
    2. catalog.resolve("Explore") -> defi
    3. background = defi.background or args.run_in_background -> False
    4. apply_agent_tool_filter(...) -> allowed
    5. 构造 sub_runtime / sub_agent（permission_mode=defi.permission_mode, dont_ask=defi.dont_ask,
       approval_upgrader=default_upgrader）
    6. sub_conv = Conversation()（空白）
    7. 前台: final_text = await asyncio.wait_for(
           sub_agent.run_to_completion(sub_conv, args.prompt, events), timeout=120)
       -> return Result(content=final_text)
    8. 超时 -> 重起协程 + adopt_running -> return Result(content='{"task_id":"...","status":"timed_out_to_background"}')
```

### 运行时：主 Agent 调 Agent 工具（后台，显式 / Fork）

```
AgentTool.execute:
    background = True（run_in_background 或 defi.is_fork()）
    ...
    task_id = await task_mgr.launch(sub_agent, sub_conv, args.name, args.prompt)
    return Result(content='{"task_id":"task_xxx","status":"async_launched"}')

Fork 特例:
    defi = catalog.fork_definition()           # name="__fork__", is_fork()=True
    forked = build_forked_messages(parent_conv.messages())   # 只含 Boilerplate user
    sub_conv = Conversation.from_messages(forked)
    task_id = await task_mgr.launch(sub_agent, sub_conv, args.name, args.prompt)
    # launch 内 runner 调 run_to_completion(conv, args.prompt, events) -> add_user(task)
```

### 后台任务完成通知

```
task_mgr.launch 协程:
    text = await ag.run_to_completion(conv, task, events)
    bt.result = text; bt.status = COMPLETED   # 或 FAILED / CANCELLED
    finally: self._done.put_nowait(task_id)   # QueueFull -> 丢弃 + stderr
    ↓
tui.run 主循环（下一轮输入时）:
    await _inject_task_notifications(task_mgr, runtime)
    → 排空 done 队列 → build_task_notification(bt) → runtime.append_reminders([notif])
    ↓
下一次 self.agent.run → take_reminders() → 拼进 reminder 区（已有 ch12 机制）
```

### Fork 子 Agent 调 Agent 工具被阻断

```
Fork 子 Agent 跑动中,LLM 又产 tool_use:{name:"Agent", ...}
    ↓
sub_agent._execute_batched → registry.execute("Agent", ...)
    ↓
registry.get("Agent") -> None    # 子 Agent 构造时工具过滤已剔除 Agent（ALL_AGENT_DISALLOWED_TOOLS）
    → 返回 "未知工具: Agent"（模型看不到该工具定义,根本不会发起,此路径为防御）
```

### Skill fork 改造

```
skills.Executor.execute("/foo") → meta.is_fork() → _run_fork → _run_subagent(ctx, skill, rendered)
    ↓ （改造后）
self._run_subagent(ctx, skill, rendered):
    if skill.meta.model:
        provider = new_provider(dataclasses.replace(provider_cfg, model=skill.meta.model))
    fork_conv = ... (skills 现状构造:recent/full 克隆尾部或空白 + add_user(rendered))
    return await launch_fork(ForkLaunchOpts(
        allowed_tools=skill.meta.allowed_tools,
        conv=fork_conv,                     # 已含任务文本
        provider=provider,
        registry=..., engine=..., version=..., hook_engine=...,
        context_window=主窗口,
    ))

agent.launch_fork 内部:构造 fork_runtime / Agent(permission_mode=Mode.BYPASS) /
  run_to_completion(conv, "", events=None) → 返回 final_text
```

`launch_fork` 与 `AgentTool.execute` Fork 分支共享同一段「构造 fork runtime + 子 Agent」的 wiring，只是后者多一层定义解析与后台管理。

## 文件组织

```
src/novacode/
├── subagent/                       ← 新增包
│   ├── __init__.py                 公共导出（Definition / Source / Catalog / load_catalog）
│   ├── definition.py               Definition / Source 类型
│   ├── parser.py                   parse_definition / parse_file
│   ├── catalog.py                  Catalog + load_catalog / resolve / list / fork_definition
│   ├── embed.py                    builtin_definitions()（importlib.resources）
│   └── builtin/
│       ├── general-purpose.md
│       ├── explore.md
│       └── plan.md
├── task/                           ← 新增包
│   ├── __init__.py                 公共导出（Manager / Status / BackgroundTask / 4 工具）
│   ├── manager.py                  Manager + BackgroundTask + PartialState + Usage
│   └── tools.py                    TaskListTool / TaskGetTool / TaskStopTool / SendMessageTool
├── agent/                          ← 现有包扩展
│   ├── __init__.py                 加 5 字段;run 循环上限改 max_turns;_execute_batched 串行分支
│   │                               加 dont_ask 短路 + approval_upgrader 调用
│   ├── run_to_completion.py        run_to_completion（async for 复用 run）
│   ├── fork.py                     build_forked_messages / is_fork_context / FORK_BOILERPLATE
│   ├── agent_tool.py               AgentTool + AgentArgs
│   ├── permission_upgrade.py       ApprovalUpgrader 类型 + default_upgrader
│   └── launch.py                   ForkLaunchOpts + launch_fork
├── tool/
│   └── filter.py                   ALL_AGENT_DISALLOWED / ASYNC_AGENT_ALLOWED / apply_agent_tool_filter
├── tui/
│   ├── run.py                      加 task_mgr/subagent_catalog 参数;agent 构造后 set_parent;
│   │                               每轮 _inject_task_notifications
│   └── tasks.py                    build_task_notification + _inject_task_notifications
├── config.py                       加 enable_subagent_background 字段 + effective_enable_subagent_background()
└── cli.py                          load_catalog / Manager / 5 工具注册;传 task_mgr / subagent_catalog 给 run

tests/                              ← 扁平（对齐现有 test_*.py 布局）
├── test_subagent_parser.py
├── test_subagent_catalog.py
├── test_subagent_builtin.py
├── test_tool_filter.py
├── test_agent_fork.py
├── test_agent_options.py
├── test_run_to_completion.py
├── test_agent_tool.py
├── test_agent_tool_integration.py
├── test_task_manager.py
├── test_task_tools.py
└── test_config.py（扩展 enable_subagent_background）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| run_to_completion 与 run 关系 | `async for` 复用 `run` 的 async generator，不重写循环 | 主对话与子 Agent 在 ReAct/hook/压缩层面行为一致；只需转发 tool/usage 事件 |
| 子 Agent 是否独立 PermissionEngine | 共享同一 `engine` 实例（父对话已批准账本自动生效）+ `permission_mode`/`dont_ask` 覆盖 | 权限规则全局一致；独立 Engine 是预留扩展点，本期不需要 |
| Fork 强制后台 | 是 | Fork 上下文长，前台同步会阻塞用户；并行 Fork 才有意义 |
| 后台通知形式 | system reminder 注入（`<task-notification>`），经 `runtime.append_reminders` | 与 ch12 通道一致；主 Agent 下次 turn 自然消费，不打断当前操作 |
| 嵌套阻断 | `ALL_AGENT_DISALLOWED_TOOLS` 工具过滤（唯一闸，含 Fork） | Tool 协议无 context，运行时检测调用方不可行；过滤是最可靠且不破坏 prompt cache 稳定性的方式 |
| `is_fork_context` | 保留为辅助/测试函数，不作运行时拦截 | 过滤已兜底；`is_fork_context` 用于 build_forked_messages 测试与 debug |
| 后台白名单粒度 | 列具体工具名 + MCP `mcp__` 前缀动态识别 | 不需要为每个 MCP 工具列在白名单里 |
| done 队列缓冲 32 | 够大 | 正常场景不会有 32 个任务同时完成；满则 `put_nowait` 抛 `QueueFull`，丢弃 + stderr |
| send_message 同 id 复用 | 是 | 语义上是"该任务继续"而非"新任务"；UI/查询更连贯 |
| 配置开关 enable_subagent_background | 默认 True | 后台是核心能力默认开启；关闭后强制前台，供 CI/调试 |
| Markdown 解析器 | subagent 包独立实现一份（参照 skills parser 思路） | 字段集不同（agent 定义 vs SKILL.md），共享收益有限，避免耦合 |
| Agent 工具 parent 注入时机 | cli 注册时 None，`tui.run` 构造主 agent 后 `set_parent` | 主 agent 在 run 内构造，cli 无法在注册时拿到；run 内从 registry.get("Agent") 回填 |
| ESC 切后台 vs Ctrl+C | **本期不做 ESC 切后台** | 实际 `_CancelWatcher` 中 ESC 已绑定取消主回复；重构语义风险高，超时+显式后台已覆盖主场景 |
| 子 Agent Ask 升级主 TUI 弹窗 | **本期不弹窗**：`approval_upgrader` 默认拒绝本次回灌 | 前台 inline 子 Agent 阻塞在主 Agent 工具执行内，事件流暂停无法弹窗；拒绝+调整是可靠退路 |
| model 字段切换 provider | **本期不切**：恒用父 provider | 单 provider 环境（deepseek），无按模型切换的 provider 配置；字段保留待多 provider |
| 并发原语 | `asyncio.Queue` / `asyncio.Task` / `asyncio.wait_for` | Python async-first 体系；与 prompt_toolkit 事件循环共存 |
| 内置 .md 加载 | `importlib.resources.files("novacode.subagent.builtin")` | 标准库官方推荐；打包成 wheel 后仍可读 |
