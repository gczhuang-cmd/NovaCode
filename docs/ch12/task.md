# Hook 生命周期挂钩系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/novacode/permission/matcher.py` | Matcher Protocol、四种实现、`compile_matcher` 工厂 |
| 新建 | `tests/test_permission_matcher.py` | 四种 type × 边界条件覆盖 |
| 修改 | `src/novacode/permission/rule.py` | `Rule.matcher` 替代 `pattern`、放宽 `_RULE_RE`、`RuleSet.match/match_deny/match_allow` 改调 matcher |
| 修改 | `tests/test_permission_rule.py` | 扩展用例覆盖新语法 |
| 修改 | `src/novacode/permission/settings.py` | `to_rule_set` 改造：失败 rule 走 stderr |
| 修改 | `tests/test_permission_settings.py` | 验证 stderr 报错与跳过逻辑 |
| 新建 | `src/novacode/hook/__init__.py` | 包标识 + 暴露 `Engine` / `Event` / `load` / `DispatchResult` |
| 新建 | `src/novacode/hook/event.py` | 11 个 `Event` 枚举 + 拦截类列表 + `is_blocking` + `parse_event` |
| 新建 | `src/novacode/hook/rule.py` | `Rule` / `Condition` / `Action` / `Payload` 数据结构 |
| 新建 | `src/novacode/hook/matcher.py` | `eval_condition` / `get_by_path` |
| 新建 | `src/novacode/hook/loader.py` | YAML 解析、双层合并、字段校验 |
| 新建 | `tests/test_hook_loader.py` | 字段校验、加载错误、合并测试 |
| 新建 | `src/novacode/hook/engine.py` | `Engine` + dispatch 主流程 + only_once |
| 新建 | `tests/test_hook_engine.py` | 各事件 dispatch、拦截、reminder、once 覆盖 |
| 新建 | `src/novacode/hook/executor.py` | 四类 action 执行器 |
| 新建 | `tests/test_hook_executor.py` | shell exit2、http block、prompt、subagent stub |
| 新建 | `tests/test_hook_event.py` | Event 枚举、is_blocking 覆盖 |
| 修改 | `src/novacode/agent/runtime.py` | `SessionRuntime` 加 `pending_reminders` + `hook_engine` 字段；`reset_for_new_session` 清空 + 转发引擎重置 |
| 修改 | `tests/test_agent_runtime.py` | 验证 `pending_reminders` 行为与 reset 清空 |
| 修改 | `src/novacode/agent/__init__.py` | `Agent.__init__(..., hook_engine=...)` 参数、各 emit 点（**Agent 类在此文件，无 agent/agent.py**） |
| 修改 | `tests/test_agent.py` | 拦截路径测试 |
| 修改 | `src/novacode/tui/run.py` | `run()` 加 `hook_engine` 参数；SessionStart 挂接；UserPromptSubmit 拦截集成 |
| 修改 | `tests/test_tui.py` | UserPromptSubmit 拦截 / SessionStart 挂接覆盖 |
| 修改 | `src/novacode/command/context.py` | `CommandContext` 加 `hook_engine` 字段 |
| 修改 | `src/novacode/command/builtin_ui.py` | `/clear`、`/resume`、`/exit` 挂 SessionEnd + SessionStart/Resume |
| 新建 | `src/novacode/command/builtin_hooks.py` | `/hooks` 命令 handler（Kind.LOCAL） |
| 修改 | `src/novacode/command/builtins.py` | `register_builtins` 加 `/hooks` |
| 修改 | `src/novacode/cli.py` | 加 `hook.load(root)` 与 wiring；SessionEnd 兜底 |
| ~~修改 pyproject.toml~~ | — | **无需改**：`httpx>=0.27` 已在 dependencies |

## T1: 实现 `permission.Matcher` 接口与四种类型

**文件：** `src/novacode/permission/matcher.py`
**依赖：** 无
**步骤：**
1. 新建 `matcher.py`，声明 `Matcher(Protocol)`，要求 `match(s: str) -> bool` 与 `__str__`
2. 实现 4 个 frozen dataclass：
   - `ExactMatcher(value: str)`：`match` 返回 `s == value`
   - `GlobMatcher(pattern: str, is_command: bool)`：内部复用 `rule.match_pattern`（命令整串 / 路径分段）；`__str__` 返回 `pattern`
   - `RegexMatcher(src: str, compiled: re.Pattern[str])`：`match` 返回 `compiled.search(s) is not None`
   - `NotMatcher(inner: Matcher)`：`match` 返回 `not inner.match(s)`
3. 实现工厂 `compile_matcher(pattern: str, *, is_command: bool) -> Matcher`：
   - 空串 → `raise ValueError("empty matcher pattern")`
   - 以 `=` 起头 → `ExactMatcher(rest)`
   - 以 `~` 起头 → `re.compile(rest)`，失败转 `ValueError`
   - 以 `!` 起头 → 递归 `compile_matcher(rest, is_command=is_command)` 包装为 `NotMatcher`
   - 其它 → `GlobMatcher(pattern, is_command)`
4. 从 `rule.py` import `match_pattern` 供 `GlobMatcher` 复用（注意：hook 上下文统一传 `is_command=False`）
5. 写 docstring 解释每个 Matcher 类型的语义

**验证：** `python -c "from novacode.permission.matcher import compile_matcher; print(compile_matcher('=foo', is_command=False))"` 输出 `=foo`

## T2: matcher 单元测试

**文件：** `tests/test_permission_matcher.py`
**依赖：** T1
**步骤：**
1. 覆盖 4 种类型各自的命中/不命中用例
2. `=git status` 命中 `git status`、不命中 `git status -s`
3. `~^npm (install|test)$` 命中 `npm install`、不命中 `npm run dev`
4. `!=foo` 不命中 `foo`、命中 `bar`
5. `!~^rm` 命中 `ls -lh`、不命中 `rm -rf .`
6. `!git *` 命中 `npm install`、不命中 `git status`（嵌套 not + glob）
7. 编译失败：`~[invalid` 应抛 `ValueError`
8. 空串：`""` 应抛 `ValueError`
9. 用 `pytest.mark.parametrize` 表驱动，每条用例附 `id=...` 描述

**验证：** `pytest tests/test_permission_matcher.py -v` 通过

## T3: 升级 `permission.Rule` 与 `parse_rule`

**文件：** `src/novacode/permission/rule.py`
**依赖：** T1
**步骤：**
1. `Rule` dataclass 改：去 `pattern` 字段、加 `matcher: Matcher | None`（`None` 表示该工具全匹配）与 `raw: str`（原始模式串）
2. **放宽 `_RULE_RE`**：现有 `^([A-Za-z][A-Za-z0-9_]*)(?:\(([^()]*)\))?$` 的模式段不含括号，无法解析 `Bash(~^npm (install|test)$)`。改为允许模式段内括号、取到最右括号（如 `^([A-Za-z][A-Za-z0-9_]*)\s*\((.*)\)\s*$`），保持工具名校验不变
3. `parse_rule` 签名**保持不变**：`def parse_rule(s: str) -> tuple[Rule, bool]`——成功 `Rule.matcher = compile_matcher(pattern, is_command=(tool == "Bash"))`；空 pattern 仍按 `None` matcher 表示「全匹配」；失败返回 `(Rule(), False)`（错误描述由调用方 `to_rule_set` 打印）
4. 改造 `RuleSet.match / match_deny / match_allow`：`r.matcher is None` 返回 True（全匹配），否则 `r.matcher.match(target)`
5. `match_pattern` / `_glob_to_regex` / `_match_segments` 保留不动（GlobMatcher 内部复用）
6. `escape_glob`（在 `persist.py`）保留不变，仅供 ch08 自动生成的精确规则使用
7. docstring 更新说明四种语法

**验证：** `pytest tests/test_permission_rule.py -v` 通过；`Bash(~^npm (install|test)$)` 能解析出 matcher 且类型为 RegexMatcher

## T4: 升级 `to_rule_set` 错误日志

**文件：** `src/novacode/permission/settings.py`
**依赖：** T3
**步骤：**
1. `to_rule_set` 改造：`parse_rule` 失败时 `print(f'rule {raw!r} parse failed: {err}', file=sys.stderr)`
2. 顶部 `import sys`；`parse_rule` 失败时用 `raw` 字段或原始串拼错误信息
3. 加注释说明：失败的 rule 不进入 RuleSet，但其它 rule 不受影响

**验证：** `python -c "from novacode.permission.settings import to_rule_set; print('ok')"` 不报错

## T5: 扩展 `test_permission_rule` 与 `test_permission_settings`

**文件：** `tests/test_permission_rule.py`、`tests/test_permission_settings.py`
**依赖：** T3、T4
**步骤：**
1. `test_permission_rule`：补充用例
   - `Bash(=git status)` 精确匹配
   - `Bash(~^npm (install|test)$)` 正则匹配（验证 `_RULE_RE` 放宽后能解析）
   - `Bash(!~^rm)` 反向正则
   - `Write(**/*.py)` glob 沿用（确认向后兼容）
2. `test_permission_settings`：用 `capsys` fixture 捕获 stderr，构造一份含非法 rule 的 yaml，验证 `to_rule_set` 返回的 RuleSet 不含该 rule（检查 `rule_set.allow` / `deny` 长度即可），且 stderr 含 `parse failed`
3. 旧测试若引 `Rule.pattern` 字段则改为断言 `matcher` / `raw`；`match_pattern` 底层函数测试保留

**验证：** `pytest tests/test_permission_rule.py tests/test_permission_settings.py -v` 全部通过

## T6: hook 包基础数据结构

**文件：** `src/novacode/hook/__init__.py`、`src/novacode/hook/event.py`、`src/novacode/hook/rule.py`
**依赖：** 无
**步骤：**
1. `__init__.py`：包标识，re-export `Engine` / `Event` / `load` / `DispatchResult`
2. `event.py`：
   - `class Event(str, enum.Enum)`，11 个成员对应 YAML 字面量（`SESSION_START = "SessionStart"` 等）
   - `BLOCKING_EVENTS: frozenset[Event] = frozenset({Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT})`
   - `def is_blocking(e: Event) -> bool: return e in BLOCKING_EVENTS`
   - `def parse_event(s: str) -> Event | None`：`Event(s)` 包 try/except `ValueError`
3. `rule.py`：
   - `Rule`、`Condition`、`AtomCondition`、`Action`、`ShellAction`、`PromptAction`、`HttpAction`、`SubagentAction`、`ActionType`、`CombineMode` 等 dataclass / 枚举
   - 类型别名 `Payload = dict[str, Any]`
   - 注意：`Rule.asyncio_mode` 字段替代 YAML 的 `async`（Python 关键字）

**验证：** `python -c "from novacode.hook import Event; print(Event.PRE_TOOL_USE.value)"` 输出 `PreToolUse`

## T7: `hook.matcher` 字段路径求值

**文件：** `src/novacode/hook/matcher.py`
**依赖：** T6、T1
**步骤：**
1. `get_by_path(p: Payload, path: str) -> str`：按 `.` 分隔；递归取值；中途遇 `None` 或非 dict 返回空串
2. 字段值非字符串时：`bool` / `int` / `float` 用 `str(value)`（`True` → `"True"`，与 N6 输出保持一致）；嵌套对象转 `json.dumps(value, sort_keys=True)`
3. `eval_condition(c: Condition | None, p: Payload) -> bool`：
   - `c is None` → True
   - 遍历 `c.atoms`，每条用 `get_by_path` + `AtomCondition.matcher.match`
   - `CombineMode.ALL_OF` 要求全部 True、`CombineMode.ANY_OF` 要求至少一个 True

**验证：** `pytest tests/test_hook_matcher.py -v` 通过（新建独立测试，或并入 `test_hook_engine.py`）

## T8: `hook.Loader` YAML 解析

**文件：** `src/novacode/hook/loader.py`
**依赖：** T6、T7、T1
**步骤：**
1. 定义 `def load(project_root: str | Path) -> Engine`：
   - 计算两个候选路径：`<project_root>/.novacode/hooks.yaml`、`Path.home() / ".novacode" / "hooks.yaml"`
   - 文件不存在跳过；存在但 `yaml.safe_load` 失败时 `print(..., file=sys.stderr)` 后跳过整文件
   - 顶层结构必须含 `hooks: list`；不合法整文件跳过并打 stderr
   - 对每条 dict 调内部 `_compile_rule(source, idx, raw) -> Rule | None`
   - 累积成功的 rule、stderr 输出失败的 rule
   - 跨文件 name 冲突时跳过后者，stderr 打提示
2. `_compile_rule` 内做字段校验：
   - `name` 必填且非空
   - `event` 枚举（用 `parse_event`，失败 → `hook "<name>": unknown event "<value>", skipped`）
   - `action.type` 枚举与子字段必填（`shell.command`、`prompt.text`、`http.url`、`subagent.agent_name` + `subagent.prompt`）
   - `if` 顶层 `all_of` / `any_of` 互斥
   - 每个原子条件的 `match.type` ∈ `{exact, glob, regex, not}` 且 `value` / `inner` 字段完整
   - `async` + `is_blocking(event)` → 报错 `hook "<name>": async not allowed for blocking events, skipped` 跳过
   - `timeout` 字符串解析：支持 `30s` / `5m` / 浮点秒；用一个小函数 `_parse_duration(s) -> float`（用 `re` 匹配 `\d+(\.\d+)?([smh]?)`），失败 → 报错跳过；缺省 30.0
3. Matcher 编译用 `permission.compile_matcher`；hook 上下文中的 matcher 都作用于 payload 字段值，统一传 `is_command=False`（glob 走文件路径语义，段内 `*` 不跨 `/`）
   - **决策修正**：`tool_input.command` 这类 shell 字符串字段如果想做整串通配，用户应改用 regex 表达；文档中注明此约束

**验证：** `python -c "from novacode.hook.loader import load; print(load('.'))"` 不抛异常

## T9: `hook.Loader` 测试

**文件：** `tests/test_hook_loader.py`
**依赖：** T8
**步骤：**
1. `tmp_path` fixture 场景：写一份合法 `hooks.yaml`（含 2 条 hook），`load` 返回 Engine 含 2 条 rule
2. 字段缺失：name 空、event 不存在、action.type 无效 → 跳过该条但其它通过
3. `all_of` + `any_of` 同时存在 → 跳过该条
4. async + PreToolUse → 跳过该条且 capsys 捕获 stderr 含 `async not allowed for blocking events`
5. 跨文件同名冲突 → 项目级保留、用户级跳过（monkeypatch `Path.home` 指向 tmp_path）
6. matcher 编译失败（非法正则） → 跳过该条

**验证：** `pytest tests/test_hook_loader.py -v` 通过

## T10: `hook.Engine` 与 dispatch 主流程

**文件：** `src/novacode/hook/engine.py`
**依赖：** T6、T7
**步骤：**
1. `Engine` 类：`_rules`、`_sources`、`_lock: asyncio.Lock`、`_once_fired: set[str]`、`_executor`
2. `__init__(self, rules: list[Rule], sources: list[str])`
3. `async def dispatch(self, event: Event, payload: Payload) -> DispatchResult`：
   - 遍历 rules，跳过非本事件
   - 加锁查 `_once_fired`，命中跳过
   - `eval_condition`；不通过跳过
   - 命中后：
     - `rule.asyncio_mode` 为 True → `asyncio.create_task(self._executor.run(rule, payload, blocking=False))`，立即继续（不等结果、不进入 `injected_prompts` 与 `blocked` 判定）；若 only_once，标记 fired
     - 同步：`await self._executor.run(rule, payload, blocking=is_blocking(event))`
   - 同步结果处理：
     - `outcome.err is not None` → stderr 日志 `[hook <name>] <event.value> failed: <reason>`，继续下一个 rule（不拦截）
     - `outcome.prompt` 非空 → 加入 `injected_prompts`
     - `outcome.blocked and is_blocking(event)` → 设置 `result.blocked` + `reason` + `blocking_hook_name`，break
   - 命中且执行无 fatal err 的 rule，若 `only_once` → 加入 `_once_fired`
4. `async def reset_for_new_session(self)`：加锁清空 `_once_fired`
5. property `sources` 与 `rules` 返回副本

**验证：** `python -c "import asyncio; from novacode.hook.engine import Engine; from novacode.hook import Event; asyncio.run(Engine([], []).dispatch(Event.STOP, {}))"` 通过

## T11: `hook.Executor` 四类动作执行

**文件：** `src/novacode/hook/executor.py`
**依赖：** T6
**步骤：**
1. `Executor` 类：仅 `_http_client: httpx.AsyncClient`
2. `__init__(self)`：`self._http_client = httpx.AsyncClient(timeout=30.0)`（单实例复用连接池）
3. `async def run(self, rule, payload, *, blocking) -> ExecutionResult` 分发到下面四个内部方法
4. `async def _run_shell(self, sa, payload, blocking, timeout)`：
   - `proc = await asyncio.create_subprocess_shell(sa.command, stdin=PIPE, stdout=PIPE, stderr=PIPE)`（Linux/WSL 走 sh；Windows 走 cmd.exe）
   - `payload_json = json.dumps(payload, sort_keys=True).encode()`
   - `stdout, stderr = await asyncio.wait_for(proc.communicate(payload_json), timeout=timeout)`
   - 超时 `asyncio.TimeoutError`：`proc.kill(); await proc.wait()`，返回 `err=TimeoutError(...)`
   - 输出解码：Windows 平台按 GBK（`_decode` 策略），其它按 UTF-8；解码失败回退 `errors="replace"`
   - `blocking and proc.returncode == 2` → `blocked=True, reason=(stderr or stdout).decode().rstrip("\n")`
   - `proc.returncode == 0` → 不拦截不报错
   - 其它非 0 returncode → `err=RuntimeError(f"exit {code}: {stderr.decode()}")`
5. `def _run_prompt(self, pa) -> ExecutionResult`：返回 `ExecutionResult(prompt=pa.text)`
6. `async def _run_http(self, ha, payload, blocking, timeout)`：
   - 默认 `method = ha.method or "POST"`
   - body：`ha.body is None` 时 `json.dumps(payload, sort_keys=True)`；否则 `ha.body.format_map(payload)`，渲染异常按 `err` 处理
   - `resp = await self._http_client.request(method, ha.url, content=body, headers=ha.headers, timeout=timeout)`
   - status 2xx 且 `json.loads(resp.text)` 含 `{"decision":"block","reason":"..."}` → `blocked=True`
   - 网络错（`httpx.HTTPError`）/ 超时（`httpx.TimeoutException`）/ JSON 解析失败 → `err`
7. `def _run_subagent(self, sa) -> ExecutionResult`：仅 `print(f"[hook subagent] not yet implemented, skipped: {sa.agent_name}", file=sys.stderr)`，返回空 `ExecutionResult()`
8. payload JSON 序列化用共享辅助 `_marshal_sorted(p) -> bytes`，保证 key 字典序

**验证：** `python -c "from novacode.hook.executor import Executor; print(Executor())"` 通过

## T12: executor 单元测试

**文件：** `tests/test_hook_executor.py`
**依赖：** T11
**步骤：**
1. shell exit 2 with stderr → blocked=True + reason 含 stderr
2. shell exit 0 → 放行不报错
3. shell exit 1 → err 非 None 不拦截
4. shell stdin JSON 解析：脚本读 stdin 后 echo 出来，验证 key 字典序（`sh -c "cat"` + 比对输出）
5. shell timeout：`sleep 2 && echo done` + timeout 0.1s → err 类型为 `TimeoutError`
6. prompt → result.prompt 字段非空
7. http with `pytest-httpserver` 或自起 `aiohttp` 桩返回 `{"decision":"block","reason":"x"}` → blocked=True
8. http with 5xx → err 非 None
9. http 模板 body 含 `{event}` → server 收到正确字段
10. subagent → capsys 捕获 stderr 含占位文本

**验证：** `pytest tests/test_hook_executor.py -v` 通过

## T13: `hook.Engine` 测试

**文件：** `tests/test_hook_engine.py`（含 `test_hook_event.py` 的 is_blocking 断言）
**依赖：** T10、T11
**步骤：**
1. 多 rule 同事件按声明序执行
2. 拦截类事件下首个 blocked 的 rule 中断后续
3. 非拦截类事件下 blocked 字段不传递（fake exit code 2 但 `is_blocking(event)=False` 也不 set `blocked`）
4. prompt rule 的 prompt 累加到 `injected_prompts`
5. only_once 在首次执行后被加入 `_once_fired`，第二次 dispatch 跳过
6. `reset_for_new_session` 后 only_once 重置
7. async rule 不进入 blocked 判定（用 `asyncio.Event` 验证 task 已起）
8. `Event` 枚举：11 个成员、`is_blocking` 仅对 PRE_TOOL_USE / USER_PROMPT_SUBMIT 返回 True、`parse_event` 未知名返回 None

**验证：** `pytest tests/test_hook_engine.py -v` 通过

## T14: agent `SessionRuntime` 扩展

**文件：** `src/novacode/agent/runtime.py`、`tests/test_agent_runtime.py`
**依赖：** T6
**步骤：**
1. `SessionRuntime` 加字段：`pending_reminders: list[str]`、`hook_engine: Engine | None`（默认 None；dataclass 加 `field(default_factory=list)`）
2. `__init__` 由 dataclass 自动处理；`pending_reminders` 初始化空 list、`hook_engine` 初始化 None
3. **现有 `reset_for_new_session(self, ses_ctx)` 是同步方法**（已存在，/clear 调用）——在其内追加：清空 `pending_reminders`；若 `hook_engine is not None` 调 `await` 不可行（同步方法），**改为不在此处同步调引擎**——引擎重置由调用方（`/clear`、`/resume` handler）在调用 `reset_for_new_session` 后另行 `await hook_engine.reset_for_new_session()`，或在 `tui/run.py` 主循环统一处理
4. 新增 `def append_reminders(self, prompts: list[str]) -> None`：追加（asyncio 单线程，无需锁——现有代码注释「asyncio 单线程，无需锁」）
5. 新增 `def take_reminders(self) -> list[str]`：取出并清空
6. 测试覆盖：`append_reminders` + `take_reminders` 单线程行为；`reset_for_new_session` 清空 `pending_reminders`

**验证：** `pytest tests/test_agent_runtime.py -v` 通过

## T15: `Agent.__init__` 加 hook_engine 与 emit 框架

**文件：** `src/novacode/agent/__init__.py`（Agent 类所在）
**依赖：** T14
**步骤：**
1. `Agent.__init__` 新增 `hook_engine: Engine | None = None` 参数，赋给 `self._hook_engine`
2. 私有方法 `async def _dispatch_hook(self, event: Event, payload: Payload) -> DispatchResult`：
   - `self._hook_engine is None` → 返回空 `DispatchResult`
   - `await self._hook_engine.dispatch(event, payload)`
   - 把 `injected_prompts` 调 `self.runtime.append_reminders`
   - 返回结果（保留 `blocked` + `reason` 供 PreToolUse 用）
3. reminder 拼接：在 `run()` 每轮 `reminder` 计算处追加 `self.runtime.take_reminders()` 的内容（拼到 plan_reminder 之后），见 T16 步骤 4

**验证：** `python -c "from novacode.agent import Agent; print(Agent)"` 通过

## T16: agent 各事件 emit 接入

**文件：** `src/novacode/agent/__init__.py`
**依赖：** T15
**步骤：**
1. 每轮 iter 顶部、`will_summarize` 判定前 `await self._dispatch_hook(Event.PRE_COMPACT, {"trigger": "auto"})`（仅当 `will_summarize` 为 True、实际将调 `_manage_context` 时 emit）；`_manage_context` 返回后 emit `Event.POST_COMPACT` 带 before/after tokens
2. `run()` 内 PTL 撞墙紧急路径：`_manage_context(..., TriggerKind.EMERGENCY)` 前后 emit PreCompact/PostCompact，trigger="emergency"
3. `run_force_compact`（/compact 手动路径）：`manage_context` 前后 emit PreCompact/PostCompact，trigger="manual"
4. `_request` 调 `provider.stream` 之前 emit `Event.PRE_USER_MESSAGE`，payload 含 conversation 末尾 user 消息；reminder 串改造：`reminder = plan_reminder + "\n\n".join(runtime.take_reminders())`（在 `run()` 每轮构造处完成）
5. `_execute_batched` 改造：
   - 只读批：在 `[i, j)` 权限预检循环里、`engine.check` 之前对每条 call emit PreToolUse；`blocked=True` 时构造 `_hook_blocked_result` 预置到 `results[k]`、不加入 `allowed`（START/END 仍按序 emit，END 的 is_error=True）
   - 串行：`engine.check` 之前 emit PreToolUse；blocked 时构造 `_hook_blocked_result`、emit PhaseStart/PhaseEnd（is_error=True）、`i += 1` continue
   - tool 拿到 result 后、emit PhaseEnd 之前 emit PostToolUse，payload 含 `tool_name`、`tool_input`、`tool_result`、`is_error`
6. `run()` 自然完成 `yield Event(done=True)` 之前调 `Event.STOP`，payload `{"iter": it}`（取消、出错路径不触发）
7. emit Approval 之前调 `Event.NOTIFICATION`，payload `{"kind":"approval", "detail": call.name}`
8. `_request` emit `Event(err=...)` 之前调 `Event.NOTIFICATION`，payload `{"kind":"stream_error", "detail": str(err)}`
9. 拦截结果整合：定义 `_hook_blocked_result(call_id, hook_name, reason) -> ToolResult`：`content=f"[hook {hook_name}] {reason}"`、`is_error=True`

**验证：** `python -c "from novacode.agent import Agent; print('ok')"` 通过

## T17: `test_agent` 拦截路径与 emit 覆盖

**文件：** `tests/test_agent.py`、`tests/test_agent_runtime.py`
**依赖：** T16
**步骤：**
1. 构造一个 fake provider + 真实 `hook.Engine` 注入合成 rules
2. 测试：PreToolUse 拦截时工具结果是 `_hook_blocked_result` 形式、PhaseStart/PhaseEnd 仍 emit
3. 测试：PreUserMessage 注入的 prompt 在下一次 `_request` 的 reminder 串中可见
4. 测试：Stop 事件在 Done 前一刻被 emit
5. 用 `pytest-asyncio` 跑 async 测试函数（`asyncio_mode = "auto"`）

**验证：** `pytest tests/test_agent.py -k hook -v` 通过

## T18: `tui.run.run` 加 hook_engine 与 SessionStart 挂接

**文件：** `src/novacode/tui/run.py`
**依赖：** T15
**步骤：**
1. `run(...)` 签名加 `hook_engine: Any = None`（`hook.Engine | None`）
2. 装配 `agent = Agent(...)` 时传 `hook_engine=hook_engine`
3. 主循环 `while True` 之前、banner 与 Agent 装配之后：若 `hook_engine is not None`，构造 `base_payload` 并 `await hook_engine.dispatch(Event.SESSION_START, payload)`；把 `injected_prompts` 写入 `runtime.append_reminders`
4. 提供 `def _base_payload(event) -> dict` 辅助：返回 `{"event": event.value, "session_id": ..., "cwd": str(root), "mode": str(mode)}`（mode 用 `str(Mode)` 展示名）

**验证：** `python -c "from novacode.tui.run import run; print(run)"` 通过

## T19: UserPromptSubmit 拦截集成

**文件：** `src/novacode/tui/run.py`
**依赖：** T18
**步骤：**
1. 主循环非命令分支改写（`dispatch_slash` 返回 False 且 text 非空时、`holder.conv.add_user(text)` 之前）：
   - 若 `hook_engine` 非 None：`result = await hook_engine.dispatch(Event.USER_PROMPT_SUBMIT, base_payload | {"prompt": text})`
   - `result.blocked` → `console.print(Text(f"[hook {result.blocking_hook_name}] {result.reason}", style="bold red"))`，`continue`（不写历史、不发起请求；非全屏架构主循环自然重画输入框）
   - 否则 `runtime.append_reminders(result.injected_prompts)` 后再走 `holder.conv.add_user(text)` 与 `_stream_reply`

**验证：** `pytest tests/test_tui.py -k prompt -v` 通过

## T20: SessionEnd / SessionResume 挂接

**文件：** `src/novacode/command/builtin_ui.py`、`src/novacode/tui/run.py`、`src/novacode/tui/resume.py`
**依赖：** T18、T19
**步骤：**
1. `CommandContext` 加 `hook_engine: Any = None` 字段（`command/context.py`）
2. `handle_clear` 内：开新会话流程前 `await hook_engine.dispatch(Event.SESSION_END, payload)`（旧会话）；`reset_for_new_session` 后 `await hook_engine.reset_for_new_session()`（清 only_once）；再 `await hook_engine.dispatch(Event.SESSION_START, payload)`（新会话）
3. `handle_resume`：`await do_resume(ctx)` 前 `await hook_engine.dispatch(Event.SESSION_END, payload)`（旧会话）；恢复成功后 `await hook_engine.dispatch(Event.SESSION_RESUME, payload)` + `reset_for_new_session()`
4. `handle_exit`：置 exit_flag 前 `await hook_engine.dispatch(Event.SESSION_END, payload)`
5. 主循环退出后（`while True` break 之后）`cli` 兜底 SessionEnd（见 T22）

**验证：** `python -c "from novacode.command.builtin_ui import handle_clear; print(handle_clear)"` 通过

## T21: `/hooks` 命令

**文件：** `src/novacode/command/builtin_hooks.py`（新建）、`src/novacode/command/builtins.py`、`src/novacode/command/context.py`
**依赖：** T6、T10、T18
**步骤：**
1. `command/context.py` 的 `CommandContext` 加 `hook_engine: Any = None` 字段（在 T20 已加，此处确认）
2. 新建 `src/novacode/command/builtin_hooks.py`，实现 `async def handle_hooks(ctx: CommandContext) -> None`：
   - `engine = getattr(ctx, "hook_engine", None)`；None 或 rules 为空时 `ctx.console.print("No hooks loaded.")`
   - 否则按 event 分组（保留 yaml 声明顺序）、每条一行 `  <name>  <event>  <action.type>  [once] [async]`
   - 末尾 `Loaded from: file1, file2`
3. `builtins.py` 的 `register_builtins` 注册 `Command("hooks", "列出已加载的 hook 列表", Kind.LOCAL, handle_hooks)`

**验证：** `pytest tests/test_command_builtins.py -k hooks -v` 通过

## T22: `cli` wiring

**文件：** `src/novacode/cli.py`
**依赖：** T8、T18
**步骤：**
1. `_amain` 中在 `permission.new_engine` 之后调 `hook_engine = hook.load(root)`
2. `await run(...)` 传 `hook_engine=hook_engine`
3. `run(...)` 返回后（`_amain` 的 `finally` 之前或 `run` 内部退出后）兜底：`if hook_engine is not None: await hook_engine.dispatch(Event.SESSION_END, base_payload)`
4. 顶部 `from . import hook`（或 `from .hook import load as hook_load`）与 `from .hook import Event`

**验证：** `python -m novacode` 启动不报错

## T23: 整体编译与测试

**文件：** —
**依赖：** T1-T22 全部
**步骤：**
1. `ruff check src tests` 通过
2. `pytest` 通过——hooks 相关测试 + 既有测试都得过

**验证：** 上述两条命令本地通过

## T24: 修复回归

**文件：** 根据测试输出决定
**依赖：** T23
**步骤：**
1. 修复 ch08 / ch11 等老测试因 Matcher 改造而失败的用例（`Rule.pattern` → `matcher` / `raw`、`RuleSet.match` 语义不变）
2. 修复 ch10 / ch11 测试因 `/hooks` 命令加入而影响排序或数量的用例
3. 重新跑全套测试

**验证：** `pytest` 全过

## T25: tmux 端到端实跑（验收 AC17 与 checklist 端到端场景）

**文件：** `.novacode/hooks.yaml` 临时测试配置
**依赖：** T23、T24
**步骤：**
1. 写测试 `hooks.yaml`：包含 AC4-AC15 各典型场景的 hook
2. tmux 新建 session 启动 `python -m novacode`
3. 依次触发：`write_file` 工具调用、含 delete 关键字的用户输入、git 命令、Stop 事件
4. 观察 stderr 日志、tool_result 内容、reminder 注入是否符合预期
5. 全程不卡顿、无未捕获异常栈
6. 注意：WSL 下 hook 的 shell 动作走 sh（正常）；若在 Windows 本机验证，shell 动作走 cmd.exe（按 cmd 语法书写命令）

**验证：** 见 checklist.md

## 执行顺序

```
T1 → T2 → T3 → T4 → T5            # permission Matcher 扩展
T6 → T7 → T8 → T9                 # hook 基础结构 + Loader
T10 → T13                         # Engine
T11 → T12                         # Executor（与 Engine 并行）
T14 → T15 → T16 → T17             # agent 接入
T18 → T19 → T20                   # tui / command 接入
T21                               # /hooks 命令
T22                               # cli wiring
T23 → T24                         # 整体编译测试
T25                               # tmux 实跑验收
```

并行机会：
- T11/T12 与 T10/T13 互不依赖，可并行
- T11 与 T8 在 T6 完成后可并行
- T17 必须在 T16 之后
- T19 之前 T18 必须先完成
- T20 与 T21 都依赖 `CommandContext.hook_engine` 字段（T18 顺带加上），可并行
