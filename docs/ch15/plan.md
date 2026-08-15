# Agent Team Plan

> 本章开发前已按项目实际修订（2026-08-13）。原文档引用 `novaCodeApp`（Textual App）、
> `tui/app.py`、`cli/__init__.py`、`config/__init__.py` 等本项目不存在的结构；本章统一按
> `tui/run.py::run()` 函数式主循环、`cli.py` 单文件、`config.py` 单文件修订。

## 架构概览

本章引入 `novacode.team` 顶层包，把 ch13 SubAgent 的「子 Agent」扩展为「Team 队员」。整体分四层:

1. **数据模型层**（`team/types.py` + `team/manager.py` + `team/persistence.py`）——Team、TeammateInfo 数据结构与持久化
2. **后端层**（`team/backend/`）——`Backend` Protocol 与三种实现 tmux / iterm2 / inprocess，屏蔽 spawn 差异
3. **协作层**（`team/mailbox/`、`team/registry/`、`team/tasks/`）——邮箱（含文件锁）、AgentNameRegistry、共享任务列表
4. **工具与集成层**（`team/tools/` + `agent` 包扩展 + `coordinator` 包）——5 个 Team 协作工具 + `Agent` 工具的 `team_name` 分支 + Coordinator Mode

Lead 仍是 `tui/run.py::run()` 内构造的主 Agent——本期 Lead 没有独立类型，通过 `coordinator.is_enabled()` 在启动时收窄其工具集即可。

依赖方向（单向，无环）:

```
tui ──→ team ──→ agent ──→ {tool, permission, llm, conversation, ...}
       │   └──→ worktree(ch14)、task(ch13)、session(ch12)、subagent(ch13)
       │
cli.py ──→ {team, agent, tui, task, worktree, subagent, ...}（装配入口）
```

- `agent` 包**定义** `TeamHook` Protocol（`agent/team_hook.py`），不 import `team`（避免环）；
  `team.Manager` **实现**该 Protocol 并被 `AgentTool.team_hook` 注入。
- `team` 包依赖 `agent`（in-process 后端构造 `agent.Agent`）——`agent` 在更底层，无环。
- 队员的 mailbox 读取经 `agent.Agent` 新增的 `pre_iteration_reminders` 闭包注入（`agent/team_mailbox.py`），
  agent 包不 import `team`，闭包由 `team` 包在 spawn 时构造。

## 核心数据结构

### `team.Team`

```python
from dataclasses import dataclass, field
from datetime import datetime
import asyncio

@dataclass
class Team:
    name: str                       # 用户给的原始名
    sanitized_name: str             # 经 sanitize 后用于路径，Team 主键
    lead_agent_id: str              # 固定 "lead"（本期 Lead = 主 Agent）
    backend: "BackendType"          # 全 team 默认后端；可被 member 覆盖
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    members: list["TeammateInfo"] = field(default_factory=list)

    # 派生路径（不持久化，构造/反序列化时填充）
    config_dir: str = ""            # <home_dir>/.novacode/teams/<sanitized>/
    config_path: str = ""           # <config_dir>/config.json
    tasks_path: str = ""            # <config_dir>/tasks.json
    mailbox_dir: str = ""           # <config_dir>/mailbox/

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
```

### `team.TeammateInfo`

```python
@dataclass
class TeammateInfo:
    name: str
    agent_id: str                   # in-process 为 task.BackgroundTask.id；Pane 为预生成 "agent-<hex>"
    agent_type: str = ""            # "" 表 Fork
    model: str = ""                 # "" 表 inherit
    worktree_path: str = ""         # 绝对路径
    branch: str = ""
    backend_type: "BackendType" = "in-process"
    pane_id: str = ""               # tmux pane id / iterm2 split id / "" for in-process
    is_active: bool | None = None   # None/True 活跃，False 空闲；不存在视为终止
    plan_mode_required: bool = False
    session_dir: str = ""           # 绝对路径
```

序列化通过手写 `to_dict` / `from_dict` 完成（F19c 的 reload 流程需要细粒度控制 `is_active` 的 None 语义）。

### `team.Manager`

```python
@dataclass
class Manager:
    teams: dict[str, Team] = field(default_factory=dict)   # 按 sanitized_name 索引
    home_dir: str = ""
    wt_mgr: "worktree.Manager" = None
    task_mgr: "task.Manager" = None
    registry: "AgentNameRegistry" = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
```

### `team.BackendType`

```python
from enum import StrEnum

class BackendType(StrEnum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"
```

### `team/backend.Backend`

```python
from typing import Protocol, Any
from dataclasses import dataclass

class Backend(Protocol):
    def type(self) -> BackendType: ...
    async def spawn(self, req: "SpawnRequest") -> tuple[str, str]: ...   # (pane_id, agent_id)
    async def wake(self, pane_id: str, agent_id: str) -> None: ...
    async def kill(self, pane_id: str, agent_id: str) -> None: ...

@dataclass
class SpawnRequest:
    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str
    session_dir: str
    agent_type: str
    model: str
    initial_prompt: str
    plan_mode_required: bool

    # in-process 专用——同进程后端直接复用这三个对象
    sub_agent: Any = None       # agent.Agent
    conv: Any = None            # conversation.Conversation
    task_mgr: Any = None        # task.Manager
```

### `team/mailbox.Message` / `Box`

```python
from enum import StrEnum
from dataclasses import dataclass, field
from typing import Any

class MessageType(StrEnum):
    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"

@dataclass
class Message:
    from_: str                       # json key "from"
    to: str
    type: MessageType
    summary: str
    content: str
    payload: dict[str, Any] | None = None
    timestamp: int = 0
    read: bool = False

class Box:
    def __init__(self, dir_: str) -> None:
        self._dir = dir_             # <team_config_dir>/mailbox/

    async def write(self, agent_id: str, msg: Message) -> None: ...
    async def read(self, agent_id: str) -> list[Message]: ...
    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]: ...
    async def mark_read(self, agent_id: str, indices: list[int]) -> None: ...
```

文件锁机制内置在 `Box` 内，所有公开方法都走锁；锁实现统一在 `team/filelock.py`（mailbox 与 tasks 共用）。

### `team/registry.AgentNameRegistry`

```python
import threading

class AgentNameRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_name: dict[str, str] = {}   # name → agent_id
        self._by_id: dict[str, str] = {}     # agent_id → name

    def register(self, name: str, agent_id: str) -> None: ...
    def unregister(self, name: str) -> None: ...
    def unregister_by_agent_id(self, agent_id: str) -> None: ...
    def resolve(self, name_or_id: str) -> str | None: ...
    def name_of(self, agent_id: str) -> str | None: ...
    def list_(self) -> dict[str, str]: ...
```

注意：本章把 `task.Manager._by_name` 委托给这套 registry——`task.Manager` 增加 `name_reg: AgentNameRegistry | None` 字段（setter `set_name_registry`），`launch` 时注册、`send_message` / `get_by_name` 优先经 `name_reg` 解析。

### `team/tasks.Store`

```python
from enum import StrEnum
from dataclasses import dataclass, field

class Status(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0

class Store:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def create(self, t: Task) -> str: ...
    async def get(self, id_: str) -> Task: ...
    async def list_(self, filter_: "Filter") -> list[Task]: ...
    async def update(self, id_: str, patch: "Patch") -> None: ...
```

### `coordinator` 包

```python
# src/novacode/coordinator/__init__.py
def is_enabled(cfg) -> bool: ...      # cfg.features.coordinator_mode and env_truthy(env)
def allowed_tools() -> list[str]: ...  # COORDINATOR_ALLOWED_TOOLS
def system_prompt_suffix() -> str: ...
```

仅 3 个纯函数 + 1 个 `env_truthy`，无状态。

## 模块设计

### `novacode.team`（顶层）
**职责:** Team / TeammateInfo / Manager 数据结构与持久化，跨子包的协调入口。
**对外接口:** `Manager(...)`、`Manager.create/get/delete/handle_task_done/poll_lead_mailboxes`、`Team.add_member/set_member_active/remove_member`
**依赖:** `agent`、`worktree`、`task`、`session`、`subagent`、`team.backend`、`team.mailbox`、`team.registry`、`team.tasks`

### `novacode.team.backend`
**职责:** 屏蔽 tmux / iterm2 / in-process spawn 差异。
**对外接口:** `Backend` Protocol、`detect() -> BackendType`、`new_backend(t: BackendType, **deps) -> Backend`
**依赖:** 各子模块独立；`inprocess.py` 依赖 `agent` 与 `task`（复用 `task_mgr.launch`），`tmux.py` / `iterm2.py` 仅依赖 stdlib + `team` 常量。
采用方案：三种后端各一个子模块（`tmux.py` / `iterm2.py` / `inprocess.py`），每个独立实现 `Backend` Protocol，工厂函数 `new_backend(...)` 接收所需依赖。

### `novacode.team.filelock`
**职责:** 跨进程文件锁（mailbox 与 tasks 共用）。
**对外接口:** `async def acquire(lock_path) -> AsyncContextManager[None]`（O_CREAT|O_EXCL 抢占、5-100ms 抖动 10 次、>10s stale、退出 unlink）。
**依赖:** 仅 stdlib。

### `novacode.team.mailbox`
**职责:** 邮箱文件 + 文件锁的读写。
**对外接口:** `Box.write/read/read_unread/mark_read`、`Message` / `MessageType` 类型
**依赖:** `team.filelock` + stdlib（`os`、`json`、`asyncio`、`pathlib`）

### `novacode.team.registry`
**职责:** Agent name ↔ agent_id 双向映射。
**对外接口:** `register/unregister/unregister_by_agent_id/resolve/name_of/list_`
**依赖:** 仅 stdlib

### `novacode.team.tasks`
**职责:** 共享任务列表的 CRUD + 依赖图维护。
**对外接口:** `Store.create/get/list_/update`、`Task`、`Filter`、`Patch` 类型
**依赖:** `team.filelock` + stdlib

### `novacode.team.tools`
**职责:** 5 个协作工具（`TeamTaskCreate`/`TeamTaskGet`/`TeamTaskList`/`TeamTaskUpdate`/`TeamSendMessage`）+ 2 个 Team 管理工具（`TeamCreate`/`TeamDelete`）。
**对外接口:** 每个工具一个工厂函数 `new_xxx_tool(mgr: team.Manager) -> tool.Tool`
**依赖:** `tool`、`team`、`team.{mailbox,registry,tasks}`

### `novacode.coordinator`
**职责:** Coordinator Mode 的开关检测、工具白名单、系统提示词。
**对外接口:** `is_enabled() -> bool`、`allowed_tools() -> list[str]`、`system_prompt_suffix() -> str`、`env_truthy(v) -> bool`
**依赖:** `config`（读 feature flag）

### `agent` 包扩展

- 新增 `agent.TeamHook` Protocol（`agent/team_hook.py`）:
  ```python
  class TeamHook(Protocol):
      # spawn_teammate 让 Agent 工具委托给 Team Manager 处理 team_name 分支。
      async def spawn_teammate(self, req: "TeamSpawnRequest") -> str: ...
      # is_teammate_context 判断当前上下文是否在某队员的执行上下文中（拦截嵌套 spawn）。
      def is_teammate_context(self, ctx) -> tuple[str, str, bool]: ...  # (team_name, backend_type, in_process)
  ```
- `AgentTool` 持一个 `team_hook: TeamHook | None` 字段（可选，None 时降级为 ch13 行为）
- `Agent.execute` 在 `team_name != ""` 时调 `team_hook.spawn_teammate`
- `agent.Agent` 新增三个构造/方法：
  - `pre_iteration_reminders: Callable[[], Awaitable[list[str]]] | None = None`（run 循环每轮 `take_reminders` 后调用）
  - `exclude_tools: list[str] = []`（run 的 defs 三分支过滤，主 Agent 排除协作工具用）
  - `set_permission_mode(mode)` / `set_allowed_tools(allowed)`（Plan 审批切模式 / 加回写工具）

### `task` 包扩展

- `task.Manager` 增加 `name_reg: AgentNameRegistry | None = None` + `set_name_registry(reg)`；`launch` 时注册，`send_message` / 查找优先用 `name_reg.resolve`
- `task.Manager` 增加 `on_task_done(fn)` 回调注册接口（可注册多个）；`launch` / `send_message` / `adopt_running` 的 runner `finally` 末尾逐个 `await` 回调
- `task.Manager.send_message(name, message)` 复用——Team 模块续派直接调它

### `tui` 包扩展

- `run()`（`tui/run.py`）新增参数 `team_mgr`、`coordinator_mode: bool`；构造主 Agent 时按 `coordinator_mode` 应用白名单 + 追加 coordinator 提示词
- 新增 `tui/leadmail.py`：`consume_lead_mail` / `build_team_update_reminder` / `LeadMessage`；`run()` 启动后台 task 轮询 Lead mailbox
- 新增 `_autonomous_turn()`：Lead 空闲收到 mailbox 消息时合成 user 消息自动开一轮 Run
- 状态栏 `status_text()` 追加 ` [COORDINATOR]` 标签（`coordinator_mode=True` 时）
- `tui/tasks.py` 保持 ch13 的 `<task-notification>` 注入（队员 in-process 完成的额外路径，不改造）

### `config.py`（单文件，非子包）

- 新增 `@dataclass class FeaturesConfig`：`coordinator_mode: bool = False` + `fork_teammate: bool = False`
- `Config` 加字段 `features: FeaturesConfig = field(default_factory=FeaturesConfig)`；`_from_dict` 解析顶层 `features:` 段

### `cli.py`（单文件，非子包）

- 加 `argparse` 解析 `--team-member` / `--team` / `--member` / `--agent-id` / `--session-dir` / `--worktree` / `--agent-type` / `--model` / `--plan-mode`
- wire `AgentNameRegistry` → `team.Manager` → 7 个新工具注册 → `AgentTool(..., team_hook=team_mgr)` → `run(..., team_mgr=..., coordinator_mode=...)`
- 若 `--team-member` flag：wire 完所有依赖后直接 `await run_team_member(...)` 并 `return`（不构造 TUI）
- 若 `coordinator.is_enabled(cfg)`：把 `COORDINATOR_ALLOWED_TOOLS` 与 `SYSTEM_PROMPT_SUFFIX` 传给 `run()`

### `team/member.py`（新建，代替原 `cli/team_member.py`）

`run_team_member` 自治循环（F19a）。cli.py 为单文件不建 `cli/` 子包，故放 `team/member.py`。

## 模块交互

### TeamCreate 调用路径

```
LLM 调 TeamCreate(team_name="demo")
  ↓
tools.TeamCreate.execute
  ↓
await team.Manager.create("demo", "", "")
  ↓
1. sanitize("demo") → "demo"
2. detect_backend() → "in-process"（WSL/Windows 无 TMUX 时）
3. mkdir ~/.novacode/teams/demo/
4. mkdir ~/.novacode/teams/demo/mailbox/
5. 写 config.json（原子）
6. team.members = [TeammateInfo(name="lead", agent_id="lead", is_active=None)]
7. teams["demo"] = team
  ↓
返回 {"team_name":"demo","backend":"in-process","config_path":"..."}
```

### Agent(team_name=...) spawn 路径

```
LLM 调 Agent(team_name="demo", subagent_type="general-purpose", name="alice", prompt="...")
  ↓
agent.AgentTool.execute
  ↓
判断 team_name != "" → 委托给 team_hook.spawn_teammate
  ↓
await team.spawn_teammate(req)   # 见 team/spawn.py
  ↓
1. manager.get("demo") 取 Team
2. 校验调用者权限（in-process 队员不许 spawn，抛 InProcessTeammateNoSpawnError）
3. catalog.resolve(agent_type) 取 SubAgentDefinition
4. member_name = req.name（或自动分配）
5. await wt_mgr.create("team-demo/" + member_name, "HEAD", False) → worktree
6. 申请 session_dir（util 函数，沿用 ch12 格式）
7. 构造 SpawnRequest（含预生成 agent_id，Pane 后端用）
8. 计算 allowed = apply_agent_tool_filter(基础) + TEAMMATE_TOOLS；plan_mode_required 时仅只读基础 + TEAMMATE_TOOLS
9. 构造 sub_agent（强制 dont_ask=True，F39a）+ sub_conv + <team-context> reminder + TeammateContext(ctx 闭包)
10. await backend.spawn(req) → (pane_id, agent_id)
11. registry.register(member_name, agent_id)
12. await team.add_member(TeammateInfo(...))   # reload_from_disk_locked 保护
  ↓
返回 {"member_name":"alice","agent_id":"...","worktree":"...","backend":"in-process"}
```

### TeamSendMessage 调用路径

```
LLM 调 TeamSendMessage(to="alice", summary="hi", message="hello")
  ↓
tools.TeamSendMessage.execute
  ↓
1. 取调用者所属 Team（ctx 的 TeammateContext，或主 Agent 走 active team）
2. resolve to: "*" → 广播；否则 registry.resolve(to) → agent_id
3. 校验消息类型权限（plan_approval_response 仅 Lead，shutdown_response 仅发给 Lead）
4. 对每个目标 agent_id:
   - await mailbox.write(agent_id, msg)
   - 取 TeammateInfo.pane_id 与 backend_type
   - 若 Pane 后端: await backend.wake(pane_id, agent_id)
   - 若目标已 stop（in-process，task_mgr.get(agent_id).status != Running）:
     - await task_mgr.send_message(member_name, message) 续派
5. 返回 {"delivered_to":["agent-xxx"],"timestamp":...}
```

### 队员 Loop 内邮箱注入

```
队员的 agent.Agent.run 每轮迭代（在调 LLM 前）:
  ↓
pre_iteration_reminders 闭包（team 包注入，见 agent/team_mailbox.py::ingest_team_mailbox）
  ↓
indices, unread = await mailbox.read_unread(agent_id)
  ↓
若 len(unread) > 0:
  reminder = build_incoming_messages_reminder(unread)   # <incoming-messages>
  返回值并入本轮 reminder
  await mailbox.mark_read(agent_id, indices)
```

`agent.Agent.run` 已有 system_reminders 注入机制（ch12 hook reminder 走同一通道）；`pre_iteration_reminders` 返回值与 `take_reminders()` 结果一起拼接，复用同一通道。

### 队员 run_to_completion 结束的通知

```
task.Manager runner（launch/send_message/adopt_running 的 finally 末尾）
  ↓
on_task_done 回调（cli 装配时注册 → team_mgr.handle_task_done）
  ↓
1. 通过 registry.name_of(agent_id) 反查 name → 查 team
2. await team.set_member_active(member_name, False)   # reload_from_disk_locked
3. await mailbox.write("lead", Message(type="text", summary="<name> idle", ...))
4. 若 Lead 是 Pane 后端: await backend.wake(lead_pane_id, "lead")
  ↓
TUI consume_lead_mail 轮询到 Lead mailbox 未读 → <team-update> reminder + 自动回合
```

### Coordinator Mode 启用路径

```
cli.main 启动时:
  ↓
if coordinator.is_enabled(cfg):
    run(..., coordinator_mode=True, coordinator_allowed=COORDINATOR_ALLOWED_TOOLS,
        coordinator_prompt=SYSTEM_PROMPT_SUFFIX)
  ↓
tui/run.py 构造主 Agent 时:
    allowed_tools=coordinator_allowed（非 None 时）+ exclude_tools=TEAMMATE_TOOLS
    sys_text += coordinator_prompt（追加）
  ↓
status_text() 渲染 " [COORDINATOR]" 标签
```

## 文件组织

```
src/novacode/team/
├── __init__.py                    — 包导出
├── types.py                       — Team / TeammateInfo / BackendType / 异常类
├── manager.py                     — Manager（create/get/delete/handle_task_done/poll_lead_mailboxes）
├── persistence.py                 — sanitize / atomic_write_json / read_json / reload_from_disk_locked
├── spawn.py                       — spawn_teammate 主流程（实现 TeamHook）
├── filelock.py                    — acquire 文件锁（mailbox 与 tasks 共用）
├── feature.py                     — fork_teammate_enabled(cfg)
├── member.py                      — run_team_member 自治循环（--team-member 子进程）
├── backend/
│   ├── __init__.py                — Backend Protocol、SpawnRequest、new_backend 工厂
│   ├── detect.py                  — detect()
│   ├── tmux.py                    — Tmux Backend
│   ├── iterm2.py                  — Iterm2 Backend
│   └── inprocess.py               — InProcess Backend
├── mailbox/
│   ├── __init__.py                — Box（read/write/read_unread/mark_read）
│   └── message.py                 — Message / MessageType
├── registry/
│   └── __init__.py                — AgentNameRegistry
├── tasks/
│   ├── __init__.py                — Task / Store
│   └── filter.py                  — Filter/Patch + is_ready 计算
└── tools/
    ├── __init__.py
    ├── team_create.py             — TeamCreate
    ├── team_delete.py             — TeamDelete
    ├── team_task_create.py        — TeamTaskCreate
    ├── team_task_get.py           — TeamTaskGet
    ├── team_task_list.py          — TeamTaskList
    ├── team_task_update.py        — TeamTaskUpdate
    └── team_send_message.py       — TeamSendMessage

src/novacode/coordinator/
└── __init__.py                    — is_enabled / allowed_tools / system_prompt_suffix / env_truthy

src/novacode/agent/
├── agent_tool.py                  — 修改：增加 team_name 参数与 team_hook 委托
├── team_hook.py                   — 新建：TeamHook Protocol、TeamSpawnRequest、TeammateContext
└── team_mailbox.py                — 新建：ingest_team_mailbox（pre_iteration_reminders 闭包）

src/novacode/agent/__init__.py     — 修改：Agent 新增 exclude_tools / pre_iteration_reminders / set_permission_mode / set_allowed_tools

src/novacode/task/manager.py       — 修改：name_reg 委托 + on_task_done 回调

src/novacode/tool/filter.py        — 修改：TEAMMATE_TOOLS 常量并入 ALL_AGENT_DISALLOWED_TOOLS

src/novacode/command/
└── builtin_team.py                — 新建：/team list/info/delete/kill（Kind.LOCAL + args_handler）

src/novacode/tui/
├── run.py                         — 修改：team_mgr / coordinator_mode 参数、主 Agent exclude_tools、_autonomous_turn、状态栏标签
└── leadmail.py                    — 新建：consume_lead_mail / build_team_update_reminder / LeadMessage

src/novacode/config.py             — 修改：FeaturesConfig（coordinator_mode / fork_teammate）
src/novacode/permission/sandbox.py — 修改：/tmp、/private/tmp 白名单

src/novacode/cli.py                — 修改：argparse flags、wire team.Manager、注册 7 个新工具、--team-member 分支

tests/
├── test_team_manager.py
├── test_team_spawn.py
├── test_team_mailbox.py           — 并发与 stale 锁测试
├── test_team_registry.py
├── test_team_tasks.py
├── test_team_filelock.py
├── test_team_backend_detect.py
├── test_team_backend_tmux.py
├── test_team_backend_inprocess.py
├── test_team_tools.py
├── test_coordinator.py
├── test_sandbox_contains.py       — /tmp 白名单
└── test_agent_team.py             — TeamHook 委托 / pre_iteration_reminders 注入
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Team 包归属 | `novacode.team` 顶层包 | 与 ch13 `subagent`、ch14 `worktree` 平级，职责清晰 |
| 协作工具命名 | 加 `Team` 前缀（`TeamTaskCreate` 等） | ch13 已注册 `TaskList/TaskGet/TaskStop/SendMessage` 后台元工具，`tool.Registry.register` 重名抛 ValueError 启动崩溃；且文档 N2 要求主 Agent 看不到协作工具，覆盖会使主 Agent 的 SendMessage/TaskGet/TaskList 变团队版 |
| 协作工具可见性 | 注册进 registry + 主 Agent `exclude_tools` 排除 + `ALL_AGENT_DISALLOWED_TOOLS` 剔除 + 队员 Team 装配 | 主 Agent 的 `exclude_tools` 只过滤 defs 三分支（不破坏 plan 只读收窄）；队员工具集由 Team 装配（`filter(基础)+TEAMMATE_TOOLS`），符合 HANDOFF ch13 设计决策 2「注册进去，不是过滤留下的」 |
| 后端三选一时机 | `detect()` 在 `TeamCreate` 时一次性决定 | 与 README 一致：不做运行时回退，行为可预测 |
| 后端实现拆分 | 各一个子模块 `tmux.py / iterm2.py / inprocess.py` | `inprocess` 需要依赖 `agent` 包，拆开避免污染其他 backend |
| Backend Protocol | 三方法 `spawn/wake/kill` | 最小集；不做 pause/resume（本期不做） |
| Lead 表示 | 不引入独立类型，Lead = `run()` 内主 Agent | 收窄改动；Coordinator Mode 在工具集层面区分 |
| 邮箱实现 | `<team_config_dir>/mailbox/<agent_id>.json` + 同名 `.lock` | 跨进程通信现成方案；in-process 与 Pane 共用一套 |
| 锁文件参数 | `os.open(O_CREAT\|O_EXCL\|O_WRONLY)`，5-100ms 抖动 10 次，>10s 视 stale | README 明定；避免雪崩；Python 无跨平台 `syscall.Flock` 等价物，故走 EEXIST 抢占 |
| filelock 归属 | 独立 `team/filelock.py`，mailbox 与 tasks 共用 | 避免 mailbox→tasks 循环依赖 |
| 任务存储 | `<team_config_dir>/tasks.json` 单文件 | Team 内任务量小（几十条），无需 DB；原子写 + 文件锁 |
| AgentNameRegistry 归属 | 独立 `team.registry` 子包，`task.Manager` 委托 | 解耦；消除 ch13 `task.Manager._by_name` 的局部状态 |
| `task.Manager` 改造 | 加 `on_task_done` 回调，Team 注册 | 依赖反转，避免 task 包反向依赖 team |
| Team 持久化原子性 | `<file>.tmp` + `os.replace` | 与 ch14 worktree session、ch12 session 一致；Python `os.replace` 跨平台原子 |
| Worktree 命名 | `team-<sanitized_team>/<member>`（嵌套 slug，`/` → `+`） | 复用 ch14 嵌套 slug 能力；不污染顶层 worktree 命名空间 |
| Member session_dir | 沿用 ch12 `<root>/.novacode/sessions/<id>/` 格式 | 复用 `session.Writer`，无需新机制；Team 删除时一并清理 |
| Coordinator 开启检测 | `cfg.features.coordinator_mode and env_truthy(env)` | README 明定双锁；一次决定不允许运行时改 |
| Coordinator 工具白名单 | 硬编码常量，启动时直接收窄主 Agent 工具集 | LLM 无法解锁，安全边界清晰 |
| Plan 审批本期形态 | 文本 Plan + Lead 用 `plan_approval_response` 回复 | 不强制结构化 Plan 类型，降低实现成本 |
| plan 队员工具集 | 只读基础 + Team 协作工具（不含写工具）；approve 后 `set_allowed_tools(全量)` + `set_permission_mode(DEFAULT)` | `Mode.PLAN` 只读收窄不含协作工具（`TeamSendMessage` 有副作用），「先规划」靠工具集装配实现 |
| Fork 队员 | 受 `FORK_TEAMMATE` flag 控制，默认关 | README 明定；避免默认带满上下文 |
| 收敛 merge | 不提供专用工具，Lead 用 Bash 自主跑 git | README 明定；LLM 解冲突 = 语义理解，这是 LLM 优势 |
| `Agent` 工具的 `team_name` 在 in-process 队员处可见性 | 参数对模型可见，但调用时拦截抛错 | 与其在 schema 层动态裁剪不如统一 schema + 运行时校验，缓存友好 |
| 队员 Loop 邮箱注入 | `Agent.pre_iteration_reminders` 闭包（team 包注入） | 不改 Loop 主流程，改动最小；agent 包不 import team |
| 主 Agent 排除协作工具 | `Agent.exclude_tools` 过滤 defs 三分支 | 不改 `allowed_tools`（会破坏 plan 只读收窄），最小侵入 |
| Lead 自动唤醒 | `consume_lead_mail` 轮询 + 输入框 `app.exit(哨兵)` + `_autonomous_turn()` | 非全屏输入框阻塞主循环，无 Textual `on_mount`/`begin_turn`；用哨兵退出输入框触发自动回合 |
| TUI Coordinator 标签 | `status_text()` 静态追加 `[COORDINATOR]` | 视觉提示，运行时不可改 |
| 多 Team 并存 | `Manager.teams` dict 支持，但 spawn 时按 `team_name` 显式选 | 灵活；典型场景同一时刻一个活跃 Team |
| Team 删除时 Worktree 处理 | `wt_mgr.remove(name, ExitOptions(discard_changes=True))`，失败只警告 | 与 ch14 退出语义一致；`force=True` 才放行 |
| 错误命名 | 自定义异常类 `TeamNotFoundError` / `TeamHasActiveMembersError` / `InProcessTeammateNoSpawnError` 等，统一基类 `TeamError` | 调用方可 `except` 判别 |
| 并发模型 | `asyncio.Lock` 保护 Team / Manager 状态；mailbox 文件锁跨进程 | 与 prompt_toolkit 的 asyncio event loop 天然契合；不引入 threading 池 |
| 子进程模型 | `python -m novacode --team-member` + `asyncio.create_subprocess_exec` | 标准 Python 启动方式，跨平台 |
| 队员 agent_id | in-process 用 `task.BackgroundTask.id`（`task_<hex>`）；Pane 预生成 `agent-<hex>`（`secrets.token_hex(7)`） | 避免 ch14 `sweep_stale` 的 `^agent-a[0-9a-f]{7}$` 模式误判（长度/首字符不同），两种格式并存由 `TeammateInfo.agent_id` 记录实际值 |
