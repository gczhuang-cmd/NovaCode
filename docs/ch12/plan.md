# Hook 生命周期挂钩系统 Plan

## 技术栈

- 语言：Python 3.12+
- TUI：**prompt_toolkit 非全屏（每轮临时 Application）+ Rich 直写终端 scrollback**（ch10 修订后的实际架构，不是 Textual）
- 配置：YAML 解析（`pyyaml`，import 名 `yaml`，用 `yaml.safe_load`）
- HTTP 客户端：`httpx`（**已在 `pyproject.toml` dependencies（`httpx>=0.27`）**，原生支持 async，与 asyncio 主循环兼容）
- 异步进程：`asyncio.create_subprocess_shell` + `asyncio.wait_for` 超时
- 模板：Python 标准库 `str.format_map`（不开放函数调用）
- 测试：`pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`）、临时目录用 `tmp_path`、HTTP 桩用 `pytest-httpserver` 或自起 `aiohttp` 桩

## 架构概览

本章拆为两个层次实现：

1. **权限匹配器升级层（`novacode.permission` 包内改造）**——把 Pattern 形态从字符串升级到结构化 `Matcher` Protocol；新增 exact/regex/not 三种实现，glob 保留作为缺省类型。改造对外仅暴露语法升级和 stderr 错误回退，运行时 Allow/Deny 语义不变。
2. **Hook 主体层（新建 `novacode.hook` 包）**——加载 YAML 规则、提供事件分派引擎、四类动作执行器；通过 11 个事件 emit 点接入 agent / tui。

模块构成：

- `permission.Matcher`（新）：匹配 Protocol + 四种实现的工厂
- `hook.Loader`（新）：YAML 解析 / 字段校验 / matcher 编译 / 双层文件合并
- `hook.Engine`（新）：事件分派、only_once 集合、动作执行器协调
- `hook.Executor`（新）：四类动作的执行入口（shell / prompt / http / subagent stub）
- `hook.matcher`（薄包装）：复用 `permission.Matcher`，做字段路径取值与匹配组合
- `agent`/`tui` 改动：在生命周期 11 个时刻调 `Engine.dispatch`
- `command`：新增 `/hooks` 内置命令

## 数据流

**启动期：**

```
cli.main（main → _amain）
  ├─ permission.new_engine(root)          # 返回 (engine, err)；用升级后的 parse_rule（stderr 报错）
  ├─ hook.load(root)                      # 扫描两层 YAML、构造 Engine
  ├─ registry / cmd_registry / runtime / writer / mem_mgr 装配（现有 ch09/ch11 流程不变）
  └─ await tui.run.run(cfg, registry, engine, runtime,
                       cmd_registry=..., writer=..., mem_mgr=..., catalog=...,
                       hook_engine=hook_engine)          # 新增参数
        ├─ 选 provider → runtime.context_window 填值
        ├─ agent = Agent(provider, registry, version, engine,
        │               runtime=runtime, ..., hook_engine=hook_engine)   # Agent 在 tui/run.py 构造
        └─ 主循环：_input_frame → dispatch_slash / 对话
```

**SessionStart emit 时机：**

```
tui/run.py::run 装配完 Agent 后、进入 while True 主循环之前
  └─ await _dispatch_hook(Event.SESSION_START, base_payload)
       （首次会话。env context 已装配完毕、首条 user 消息尚未进入）
```

`/clear` 后 SessionStart：`command/builtin_ui.py::handle_clear` 内，开新会话完成后 emit。

**UserPromptSubmit 路径（tui/run.py 主循环）：**

```python
if await dispatch_slash(ctx, cmd_registry, text):
    continue
if not text:
    continue
# Hook：UserPromptSubmit 拦截点——在 conv.add_user 之前
result = await hook_engine.dispatch(Event.USER_PROMPT_SUBMIT, base_payload | {"prompt": text})
if result.blocked:
    # 非全屏：打印提示后 continue，主循环自然重画输入框；消息不写历史、不发起请求
    console.print(Text(f"[hook {result.blocking_hook_name}] {result.reason}", style="bold red"))
    continue
runtime.append_reminders(result.injected_prompts)
holder.conv.add_user(text)
# ... 现有 _stream_reply 流程
```

**PreToolUse 拦截路径（agent/__init__.py `_execute_batched`）：**

```python
# 只读批（连续只读合并）：在 [i, j) 权限预检循环里、engine.check 之前对每条 call dispatch
for k in range(i, j):
    res = await self._dispatch_hook(Event.PRE_TOOL_USE,
        {"tool_name": calls[k].name, "tool_input": parsed_input, ...})
    if res.blocked:
        results[k] = hook_blocked_result(calls[k].id, res.blocking_hook_name, res.reason)
        # START/END 仍在下方按序 emit（含 is_error=True），用户看到工具被尝试
    else:
        allowed.append(k)
# 串行（有副作用）：check 之前 dispatch；blocked 则跳过权限与执行
decision, reason = self._engine.check(mode, call, False)   # hook 拦截后不再走到这里
```

**PostToolUse 路径：** 工具拿到 result 后、emit PhaseEnd 之前 dispatch，payload 含 `tool_name` / `tool_input` / `tool_result` / `is_error`。

**Reminder 注入路径（agent/__init__.py `run` 每轮顶部）：**

```python
reminder = ""
if mode == Mode.PLAN:
    full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
    reminder = prompt.plan_reminder(full)
reminder += join_pending_reminders(self.runtime)  # 取出并清空 runtime.pending_reminders
# ... 传给 self._request(..., reminder=reminder) → llm.Request.reminder
```

注：当前 `reminder` 仅 PLAN 模式非空；hook 注入的 prompt 在非 plan 模式同样拼入，经 `llm.Request.reminder` 由 provider 作为 system-reminder 注入。

## 核心数据结构与接口

### `permission.Matcher`

```python
# novacode/permission/matcher.py（新增）
from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Protocol

class Matcher(Protocol):
    """规则匹配的统一接口；四种实现：ExactMatcher / GlobMatcher / RegexMatcher / NotMatcher。"""

    def match(self, s: str) -> bool: ...
    def __str__(self) -> str: ...   # 调试 / /hooks 输出用

@dataclass(frozen=True)
class ExactMatcher:
    value: str

    def match(self, s: str) -> bool:
        return s == self.value

    def __str__(self) -> str:
        return f"={self.value}"

@dataclass(frozen=True)
class GlobMatcher:
    pattern: str
    is_command: bool          # True 走命令整串 glob，False 走文件路径分段 glob（沿用 match_pattern 语义）

    def match(self, s: str) -> bool:
        return match_pattern(self.pattern, s)  # 复用 permission/rule.py::match_pattern

    def __str__(self) -> str:
        return self.pattern

@dataclass(frozen=True)
class RegexMatcher:
    src: str
    compiled: re.Pattern[str]

    def match(self, s: str) -> bool:
        return self.compiled.search(s) is not None

    def __str__(self) -> str:
        return f"~{self.src}"

@dataclass(frozen=True)
class NotMatcher:
    inner: Matcher

    def match(self, s: str) -> bool:
        return not self.inner.match(s)

    def __str__(self) -> str:
        return f"!{self.inner}"

def compile_matcher(pattern: str, *, is_command: bool) -> Matcher:
    """
    解析单条匹配描述串，返回 Matcher。失败抛 ValueError。
    描述串规则：
      "=value"  -> ExactMatcher
      "~regex"  -> RegexMatcher
      "!inner"  -> NotMatcher(compile_matcher(inner))
      "value"   -> GlobMatcher(沿用 match_pattern 语义)
    Bash 工具沿用命令整串 glob(is_command=True)，其它沿用文件路径 glob。
    """
    if not pattern:
        raise ValueError("empty matcher pattern")
    head, rest = pattern[0], pattern[1:]
    if head == "=":
        return ExactMatcher(rest)
    if head == "~":
        try:
            return RegexMatcher(rest, re.compile(rest))
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
    if head == "!":
        return NotMatcher(compile_matcher(rest, is_command=is_command))
    return GlobMatcher(pattern, is_command)
```

### `permission.Rule`（改造）

现有 `Rule` 字段为 `tool` / `pattern` / `allow`，`parse_rule(s) -> tuple[Rule, bool]`，`RuleSet` 提供 `match` / `match_deny` / `match_allow`（内部调 `match_pattern`）。本期改造：

```python
@dataclass
class Rule:
    tool: str                    # 不变
    matcher: Matcher | None      # 替换原 pattern 字符串；None 表示"该工具全匹配"
    allow: bool
    raw: str                     # 原始模式串（"=git status" 等），仅供错误日志与调试

# parse_rule 签名保持不变（返回 (Rule, bool)）：成功时 Rule.matcher 为编译结果，
# 失败时返回 (Rule(), False)，由调用方 to_rule_set 打 stderr 后跳过（F4）
```

注意：
- **`_RULE_RE` 需放宽**：现有 `^([A-Za-z][A-Za-z0-9_]*)(?:\(([^()]*)\))?$` 的模式段不含括号，无法解析 `Bash(~^npm (install|test)$)`。改为非贪婪取到最右括号、允许模式段内括号（如 `^([A-Za-z][A-Za-z0-9_]*)\s*\((.+)\)\s*$` 配合 rsplit 或允许嵌套的正则）。
- `match_pattern` / `_glob_to_regex` 保留（GlobMatcher 内部复用）。
- `RuleSet.match` 改造为调用 `r.matcher.match(target)`（`matcher is None` 恒命中）；`match_deny` / `match_allow` 同步改。

### `hook.Rule`

```python
# novacode/hook/rule.py
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

class Event(str, enum.Enum):
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    SESSION_RESUME = "SessionResume"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    PRE_USER_MESSAGE = "PreUserMessage"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"

BLOCKING_EVENTS: frozenset[Event] = frozenset({Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT})

def is_blocking(e: Event) -> bool:
    return e in BLOCKING_EVENTS

class CombineMode(str, enum.Enum):
    ALL_OF = "all_of"
    ANY_OF = "any_of"

class ActionType(str, enum.Enum):
    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"

@dataclass
class AtomCondition:
    field: str                   # 形如 "tool_input.path"
    matcher: "Matcher"           # 复用 permission.Matcher

@dataclass
class Condition:
    mode: CombineMode            # CombineMode.ALL_OF 或 ANY_OF；二选一不混用
    atoms: list[AtomCondition]

@dataclass
class ShellAction:
    command: str

@dataclass
class PromptAction:
    text: str

@dataclass
class HttpAction:
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None      # 模板字符串，None 表示用 payload JSON

@dataclass
class SubagentAction:
    agent_name: str
    prompt: str

@dataclass
class Action:
    type: ActionType
    shell: ShellAction | None = None
    prompt: PromptAction | None = None
    http: HttpAction | None = None
    subagent: SubagentAction | None = None

@dataclass
class Rule:
    name: str
    event: Event
    action: Action
    condition: Condition | None = None      # None 表示无条件
    only_once: bool = False
    asyncio_mode: bool = False               # 对应 YAML 的 `async`（避免与关键字冲突）
    timeout_s: float = 30.0
    source: str = ""                         # 来源文件路径，供 /hooks 显示

# Payload 是事件分派时携带的上下文数据；条件求值与动作输入都用它。
# 序列化为 JSON 时保证 key 字典序（N6）用 json.dumps(payload, sort_keys=True)。
Payload = dict[str, Any]
```

### `hook.Engine`

```python
# novacode/hook/engine.py
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

@dataclass
class DispatchResult:
    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)

class Engine:
    def __init__(self, rules: list[Rule], sources: list[str]) -> None:
        self._rules = rules                       # 按加载顺序
        self._sources = sources                   # 加载来源文件列表，供 /hooks 显示
        self._once_fired: set[str] = set()        # only_once 已触发的 hook name
        self._lock = asyncio.Lock()
        self._executor = Executor()

    async def dispatch(self, event: Event, payload: Payload) -> DispatchResult:
        result = DispatchResult()
        for rule in self._rules:
            if rule.event is not event:
                continue
            async with self._lock:
                if rule.only_once and rule.name in self._once_fired:
                    continue
            if not eval_condition(rule.condition, payload):
                continue

            if rule.asyncio_mode:
                # async hook：起 task 后立即继续，不参与 Blocked / InjectedPrompts
                asyncio.create_task(self._executor.run(rule, payload, blocking=False))
                if rule.only_once:
                    async with self._lock:
                        self._once_fired.add(rule.name)
                continue

            outcome = await self._executor.run(rule, payload, blocking=is_blocking(event))
            if outcome.err is not None:
                print(
                    f"[hook {rule.name}] {event.value} failed: {outcome.err}",
                    file=sys.stderr,
                )
                continue
            if outcome.prompt:
                result.injected_prompts.append(outcome.prompt)
            if rule.only_once:
                async with self._lock:
                    self._once_fired.add(rule.name)
            if outcome.blocked and is_blocking(event):
                result.blocked = True
                result.reason = outcome.reason
                result.blocking_hook_name = rule.name
                break
        return result

    async def reset_for_new_session(self) -> None:
        async with self._lock:
            self._once_fired.clear()

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)
```

Dispatch 内部流程：
1. 过滤匹配 event 的 rule
2. 跳过 `_once_fired` 中已触发的 only_once rule
3. 串行求值 if 条件
4. 命中条件后按 action.type 分发到 Executor
5. async rule 起 asyncio task、立即往下走
6. 同步 rule 等结果，拦截类事件下若 outcome 表达 block，累加到 DispatchResult，跳过后续同事件 rule
7. prompt 类 rule 把 text 累加到 `injected_prompts`

### `hook.Executor`

```python
# novacode/hook/executor.py
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass

import httpx

@dataclass
class ExecutionResult:
    blocked: bool = False
    reason: str = ""
    prompt: str = ""              # 仅 prompt 动作非空
    err: Exception | None = None  # hook 自身失败（不拦截）

class Executor:
    def __init__(self) -> None:
        # 默认 timeout=30s，可被 rule.timeout_s 覆盖
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def run(self, rule: Rule, payload: Payload, *, blocking: bool) -> ExecutionResult:
        action = rule.action
        if action.type is ActionType.SHELL:
            return await self._run_shell(action.shell, payload, blocking, rule.timeout_s)
        if action.type is ActionType.PROMPT:
            return ExecutionResult(prompt=action.prompt.text)
        if action.type is ActionType.HTTP:
            return await self._run_http(action.http, payload, blocking, rule.timeout_s)
        if action.type is ActionType.SUBAGENT:
            print(
                f"[hook subagent] not yet implemented, skipped: {action.subagent.agent_name}",
                file=sys.stderr,
            )
            return ExecutionResult()
        return ExecutionResult(err=RuntimeError(f"unknown action type: {action.type}"))
```

`_run_shell` 关键点：
- 调 `asyncio.create_subprocess_shell(sa.command, stdin=PIPE, stdout=PIPE, stderr=PIPE)`（Linux/WSL 走 sh；**Windows 走 cmd.exe**，stdout/stderr 按 GBK 解码，对齐 bash 工具 `_decode`）
- `payload_json = json.dumps(payload, sort_keys=True).encode()` 写到 stdin
- `await asyncio.wait_for(proc.communicate(input=...), timeout=timeout_s)`，超时 → 杀子进程并按失败处理
- `blocking and proc.returncode == 2` → `blocked=True`、`reason=(stderr or stdout).decode().rstrip("\n")`
- `proc.returncode == 0` → 不拦截不报错
- 其它非 0 returncode → `err=RuntimeError(f"exit {code}: {stderr.decode()}")`

`_run_http` 关键点：
- 默认 method=POST
- body：缺省时 `json.dumps(payload, sort_keys=True)`；否则 `ha.body.format_map(payload)`
- 用 `httpx.AsyncClient.request(method, url, content=body, headers=headers, timeout=timeout_s)`
- status 2xx 且 body 含 `{"decision":"block","reason":"..."}` → `blocked=True`
- 网络错/超时/JSON 解析失败 → `err`

## 模块设计

### 模块 A：`permission.Matcher`
**职责：** 提供四种匹配类型的统一接口；`compile_matcher` 解析前缀。
**对外接口：** `Matcher` Protocol、`compile_matcher(pattern: str, *, is_command: bool) -> Matcher`。
**依赖：** Python 标准库 `re`、`fnmatch`；复用 `permission/rule.py::match_pattern`。
**改动文件：** 新增 `src/novacode/permission/matcher.py`；改造 `src/novacode/permission/rule.py`（`Rule.matcher`、放宽 `_RULE_RE`、`RuleSet.match` 改调 matcher）。

### 模块 B：permission 错误日志
**职责：** `parse_rule` 失败时 stderr 打印失败规则与原因，原本静默跳过改为有声跳过（F4）。
**对外接口：** `to_rule_set` 内部行为变化，外部 API 不变。
**依赖：** 模块 A。
**改动文件：** `src/novacode/permission/settings.py`。

### 模块 C：`hook.Loader`
**职责：** 扫描两层 YAML 文件、解析顶层 `hooks:` 数组、字段校验、Matcher 编译、合并去重。
**对外接口：** `load(project_root: str | Path) -> Engine`——返回引擎（内部已含来源文件列表）；所有错误走 stderr 不抛异常。
**依赖：** 模块 A、`pyyaml`、`hook.Engine`。
**校验项：** name 必填 + 跨文件冲突、event 枚举、if 顶层 all_of/any_of 互斥、action.type 枚举与子字段、async + 拦截事件冲突、Matcher 编译失败、timeout 字符串格式合法。

### 模块 D：`hook.Engine`
**职责：** Dispatch 流程编排、only_once 集合管理、`reset_for_new_session`。
**对外接口：** 见上一节 Engine 类。
**依赖：** 模块 E。

### 模块 E：`hook.Executor`
**职责：** 四类动作的执行——shell（`asyncio.create_subprocess_shell` + stdin JSON + returncode 2 拦截）、prompt（直接返回 `injected_prompt`）、http（POST JSON + decision=block 解析）、subagent（stub 占位日志）。
**对外接口：** `run(rule, payload, *, blocking) -> ExecutionResult`。
**依赖：** `asyncio`、`httpx`、`json`、`str.format_map`。

### 模块 F：`hook.matcher` 包装
**职责：** 把 `permission.Matcher` 应用到 payload 的字段路径上。
**对外接口：** `eval_condition(cond: Condition | None, payload: Payload) -> bool`、`get_by_path(payload: Payload, path: str) -> str`。
**依赖：** 模块 A。

### 模块 G：agent 接入
**职责：** 在 `Agent.run` / `_request` / `_execute_batched` 等关键路径调 `Engine.dispatch`；处理 PreToolUse 拦截、注入 reminder。
**对外接口：** `Agent.__init__(..., hook_engine: Engine | None = None)`；私有方法 `_dispatch_hook(event, payload) -> DispatchResult`、`_hook_blocked_result(...) -> ToolResult`。
**依赖：** 模块 D。
**改动文件：** `src/novacode/agent/__init__.py`、`src/novacode/agent/runtime.py`（`SessionRuntime` 加 `pending_reminders: list[str]`、`hook_engine`，`reset_for_new_session` 清空 + 转发引擎重置）。

### 模块 H：tui / command 接入
**职责：** SessionStart / SessionEnd / SessionResume / UserPromptSubmit 在 `tui/run.py` 主循环与命令 handler 侧 emit；UserPromptSubmit 拦截集成到主循环（`conv.add_user` 之前）。
**对外接口：** `tui.run.run(...)` 新增 `hook_engine` 参数；`CommandContext` 新增 `hook_engine` 字段（供 `/hooks` 访问）。
**依赖：** 模块 D。
**改动文件：** `src/novacode/tui/run.py`、`src/novacode/command/builtin_ui.py`（/clear、/resume、/exit 挂 SessionEnd/Start/Resume）、`src/novacode/command/context.py`（加 `hook_engine` 字段）。

### 模块 I：`/hooks` 命令
**职责：** 输出已加载 hook 列表 + 加载来源文件。
**对外接口：** 在 `command/builtins.py::register_builtins` 注册 `Command("hooks", ..., Kind.LOCAL, handler)`。
**依赖：** 经 `CommandContext.hook_engine` 读取（`engine.sources` / `engine.rules`；None 时输出 `No hooks loaded.`）。
**改动文件：** `src/novacode/command/builtin_hooks.py`（新建，handler）、`src/novacode/command/builtins.py`。

### 模块 J：cli wiring
**职责：** 在 `cli._amain` 中调 `hook.load(project_root)`，把 Engine 注入 `tui.run.run`；退出时兜底 SessionEnd。
**改动文件：** `src/novacode/cli.py`。

## 文件组织

```
D:\NovaCode\
├── pyproject.toml                     # 已含 httpx>=0.27，无需改
├── src/novacode/
│   ├── permission/
│   │   ├── __init__.py
│   │   ├── matcher.py                 # 新增：Matcher Protocol 与四种实现
│   │   ├── rule.py                    # 改造：Rule.matcher、放宽 _RULE_RE、RuleSet.match 改调 matcher
│   │   ├── settings.py                # 改造：to_rule_set 报 stderr
│   │   └── persist.py                 # 不动（escape_glob 保留）
│   ├── hook/                          # 全新包
│   │   ├── __init__.py                # 暴露 Engine / Event / load / DispatchResult
│   │   ├── event.py                   # 11 个 Event 枚举 + 拦截类列表 + is_blocking + parse_event
│   │   ├── rule.py                    # Rule / Condition / Action / Payload 数据结构
│   │   ├── matcher.py                 # eval_condition / get_by_path（复用 permission.Matcher）
│   │   ├── loader.py                  # YAML 解析、字段校验、双层合并
│   │   ├── engine.py                  # Engine + dispatch 主流程 + only_once 集合
│   │   └── executor.py                # 四类 action 执行器
│   ├── agent/
│   │   ├── __init__.py                # Agent：增 hook_engine 参数与各 emit 点（注意：Agent 类在此文件，无 agent.py）
│   │   └── runtime.py                 # SessionRuntime 加 pending_reminders、hook_engine；reset_for_new_session 清空
│   ├── command/
│   │   ├── builtins.py                # register_builtins 加 /hooks
│   │   ├── builtin_hooks.py           # 新增：/hooks handler
│   │   ├── context.py                 # CommandContext 加 hook_engine 字段
│   │   └── builtin_ui.py              # /clear、/resume、/exit 挂 SessionEnd/Start/Resume
│   ├── tui/
│   │   ├── run.py                     # run() 加 hook_engine 参数；SessionStart 挂接；UserPromptSubmit 拦截
│   │   └── ...                        # （无 app.py / stream.py / hooks.py）
│   └── cli.py                         # 加 hook.load(root) 与 wiring；退出兜底 SessionEnd
├── tests/                             # 扁平结构（对齐现有 tests/test_*.py）
│   ├── test_permission_matcher.py     # 四种 type 覆盖
│   ├── test_permission_rule.py        # parse_rule 新语法 + _RULE_RE 放宽
│   ├── test_permission_settings.py    # to_rule_set stderr
│   ├── test_hook_event.py             # Event 枚举 / is_blocking
│   ├── test_hook_loader.py            # 校验项覆盖
│   ├── test_hook_matcher.py           # 字段路径求值
│   ├── test_hook_engine.py            # 各事件 dispatch + 拦截 + reminder + once 覆盖
│   ├── test_hook_executor.py          # shell exit2 / http block / prompt / subagent stub 覆盖
│   ├── test_agent_runtime.py          # pending_reminders / reset 覆盖（扩展现有文件）
│   ├── test_agent.py                  # 拦截路径 / emit 覆盖（扩展现有文件）
│   └── test_tui.py                    # UserPromptSubmit 拦截 / SessionStart 挂接（扩展现有文件）
└── docs/ch12/
    ├── spec.md
    ├── plan.md
    ├── task.md
    └── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 匹配前缀语法 | `=` 精确、`!` 反向、`~` 正则、无前缀=glob | 单字符前缀让既有 `Bash(git *)` 这种写法继续 work；用户写新形式时直观（=foo 一眼就是精确） |
| 反向类型嵌套 | `!=value`、`!~regex`、`!glob` 都合法 | 反向是一元运算，对内层 matcher 取反；嵌套写法直接，不需要 `not()` 函数语法 |
| Matcher 用 Protocol + dataclass | 而非 enum + match-case | Protocol 与 frozen dataclass 组合贴合 Python 习惯，Matcher 不可变；新增类型时只要实现 `match` / `__str__` 即可 |
| Hook 包独立 | `novacode.hook` | 与 `novacode.permission` 平级；hook 依赖 `permission.Matcher`，但 permission 不依赖 hook，无循环 |
| Event 用 `str` 枚举 | `class Event(str, enum.Enum)` | YAML 字面量（SessionStart 等）与 enum value 直接对应；`Event("SessionStart")` 反查方便；日志可读 |
| Payload 用 dict[str, Any] | 而非 dataclass | 11 个事件字段差异大；dict + `get_by_path` 灵活；`json.dumps(..., sort_keys=True)` 天然有序 |
| Reminder 注入用 SessionRuntime 而非 Engine 状态 | `runtime.pending_reminders` | 与现有 plan reminder 同一注入点（`Agent.run` 每轮 reminder 构造处）；下一轮自动清空；不污染 Engine |
| PreToolUse 拦截位置 | 权限 check 之前 | 让用户能用 hook 早于权限引擎做安全策略；hook 拦截后甚至不调权限 check |
| shell 用 create_subprocess_shell | 而非 list 形式 | 用户写 hook 时常用 `\|`、`>` 这种 shell 语法；直接交给系统解释器（Linux/WSL sh、Windows cmd.exe） |
| HTTP 默认 POST + JSON body | 而非 GET | hook 多是「事件通知」语义，POST 更合理；用户需要 GET 时显式声明 method |
| HTTP body 用 `str.format_map` | 不开放 Jinja2 等函数 | `format_map` 已经够覆盖字段插值；不引入额外依赖，也避免模板注入风险 |
| HTTP 客户端用 httpx | 而非标准库 urllib | 原生 async，与 asyncio 主循环兼容；`httpx.AsyncClient` 复用连接池；**已在 pyproject.toml 依赖中** |
| subagent 占位仅打日志 | 不抛异常也不阻塞 | spec 明确本期不实现，但配置应能加载——避免用户写早期配置后续章节直接生效 |
| only_once 用内存 set | 不写盘 | spec N5 明确本期不持久化；set 在引擎里，SessionRuntime 转发重置，与 ActiveSkills 同生命周期 |
| 事件分派同步串行 | 多 hook 不并发 | 拦截语义需要顺序；同步 stderr 日志顺序也确定；async hook 单独起 task 但 dispatch 不等 |
| 拦截类 sync timeout 不全局上限 | 单条 hook timeout 累加 | 用户配的 timeout 自己负责；全局上限会引入复杂语义 |
| 字段名 `asyncio_mode` 替代 `async` | 避免与 Python 关键字冲突 | YAML 里仍写 `async: true`，Loader 内部映射到 `Rule.asyncio_mode`；dataclass 字段名要合法 |
| `/hooks` 命令风格 | 与 `/skill` 对齐 | 已加载条目按事件分组、每条一行；末尾标加载来源；handler 放 `command/builtin_hooks.py`，经 `CommandContext.hook_engine` 访问 |
| 加载来源记录 | `engine._sources: list[str]` | YAML 文件路径列表，`/hooks` 命令通过 `engine.sources` 取出展示 |
| Hook 引擎装配 | cli 加载 → `tui.run.run` 注入 Agent 与 CommandContext | 实际架构无 App 类/UI Protocol；命令经 CommandContext 访问能力（ch10 既定约束，不引入 Textual/UI Protocol） |
| UserPromptSubmit 拦截展示 | 拦截后 `console.print` 提示 + `continue` | 非全屏架构输入框每轮重建，已提交文本不保留；拦截即不写历史、不发起请求，主循环自然重画输入框 |
| parse_rule 模式段放宽 | `_RULE_RE` 允许模式段内括号 | `Bash(~^npm (install|test)$)` 这类正则含括号，现有 `[^()]*` 会解析失败（F2/AC2 前置） |
