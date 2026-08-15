~~~Markdown
# slash命令体系 Tasks

> 包名：`novacode`（Python 3.12+）。源码位于 `src/novacode/`，内部模块以 `novacode.xxx` 导入；单测放在 `tests/`，运行框架 `pytest`。
> ch10 修订：TUI 为 prompt_toolkit 非全屏 + Rich scrollback，无 Textual `app.py`/`stream.py`；`prompt` 是包不是单文件；`tool.Registry` 在 `tool/__init__.py`。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/novacode/command/__init__.py` | 包出口,re-export 公共类型与函数 |
| 新建 | `src/novacode/command/command.py` | Kind 枚举、Command 与 Handler 类型 |
| 新建 | `src/novacode/command/context.py` | CommandContext（从 tui/commands.py 迁入并扩展） |
| 新建 | `src/novacode/command/registry.py` | Registry: register/lookup/visible/prefix_match + 冲突检测 |
| 新建 | `tests/test_command_registry.py` | 注册、冲突、前缀匹配、visible 排序的单测 |
| 新建 | `src/novacode/command/dispatch.py` | parse(input_text) — 解析 `/<name>` 形态 |
| 新建 | `tests/test_command_dispatch.py` | parse 各种输入的单测 |
| 新建 | `src/novacode/command/builtin_local.py` | 5 条纯本地命令(/help /status /memory /permission /session) |
| 新建 | `src/novacode/command/builtin_ui.py` | 5 条影响界面命令(/exit /plan /compact /resume /clear) |
| 新建 | `src/novacode/command/builtin_prompt.py` | 2 条提示词命令(/do /review) + REVIEW_DIRECTIVE 常量 |
| 新建 | `src/novacode/command/builtins.py` | register_builtins(reg) 把 12 条命令一次性注入 |
| 新建 | `tests/test_command_builtins.py` | 12 条命令注册成功、假 CommandContext 调用全部 handler 不抛 |
| 改造 | `src/novacode/tui/commands.py` | 删旧 CommandContext/BUILTIN_COMMANDS/dispatch_command/handle_*;仅保留 format_compact_notice |
| 新建 | `src/novacode/tui/completion.py` | CommandCompleter + bind_completion_keys（prompt_toolkit 原生补全） |
| 改造 | `src/novacode/tui/run.py` | 主循环接入 cmd_registry/usage_box; _input_frame 启用补全; 分发走 command 包 |
| 改造 | `src/novacode/cli.py` | 构造 cmd_registry + register_builtins, 传入 run() |
| 改造 | `src/novacode/prompt/__init__.py` | render_banner 就绪行只保留 /help 引导 |
| 改造 | `src/novacode/agent/runtime.py` + `tests/test_agent_runtime.py` | 新增 reset_for_new_session |
| 改造 | `src/novacode/memory/manager.py` + `tests/test_memory.py` | 新增 list_files |
| 改造 | `src/novacode/session/writer.py` + `tests/test_session.py` | 新增 path 属性 |
| 改造 | `src/novacode/tool/__init__.py` + `tests/test_tool.py` | Registry 新增 count() |
| 新增 | `tests/test_tui_commands.py` | 分发流程、大小写、未知命令、help 列表等 TUI 层分发测试 |

## 任务
### T0a: memory.Manager.list_files
**文件**：`src/novacode/memory/manager.py`、`tests/test_memory.py`
**依赖**：无
**步骤**：
1. 在 manager.py 新增 `def list_files(self) -> tuple[list[str], list[str]]` — 列出项目层与用户层 memory 目录下的 `.md` 文件(含 `MEMORY.md` 自身);目录不存在视为空 list,不抛异常;其它 `OSError` 用 `logging.warning(...)` 记录后视为空 list;返回值已按文件名字典序排序
2. 单测覆盖 4 个 case:目录不存在 / 仅含 MEMORY.md / 含多 .md / 含 .md 与非 .md 混合

**验证**：`pytest tests/test_memory.py -k list_files -v` 全绿

### T0b: session.Writer.path
**文件**：`src/novacode/session/writer.py`、`tests/test_session.py`
**依赖**：无
**步骤**：
1. `Writer` 类新增 `self.path: str` 属性;`__init__` / `open_existing` 在打开成功后写 `self.path = str(<conversation.jsonl 绝对路径>)`
2. (若已存在以 `path` 命名的字段,直接复用即可,不重复添加)
3. 单测:创建 writer 后断言 `writer.path` 非空且对应文件存在

**验证**：`pytest tests/test_session.py -k path -v` 全绿
**注**:`session_id` 不由 writer 提供,数据源是 `ctx.runtime.session.session_id`

### T0c: SessionRuntime.reset_for_new_session + tool.Registry.count
**文件**：`src/novacode/agent/runtime.py`、`tests/test_agent_runtime.py`、`src/novacode/tool/__init__.py`、`tests/test_tool.py`
**依赖**：无
**步骤**：
1. runtime.py 新增 `def reset_for_new_session(self, ses_ctx: SessionContext) -> None` — 原子重置 `replacement` / `recovery` / `auto_tracking` / `session` / `usage_anchor` / `anchor_msg_len` / `turn_count`,把 `self.session` 指向 `ses_ctx`;`context_window` 保留
2. test_agent_runtime.py 单测:调用后所有字段回到 `__init__` 时的零值,`session` 属性被替换
3. tool/__init__.py 的 `Registry` 新增 `def count(self) -> int` — 返回 `len(self._order)`(O(1))

**验证**：`pytest tests/test_agent_runtime.py -k reset -v` 全绿;`python -c "from novacode.tool import Registry; Registry().count()"` 不报错

### T1: 定义 Command 类型与 Kind 枚举
**文件**：`src/novacode/command/command.py`
**依赖**：无（先建 T0c 同步的 context.py 骨架,或先写 command.py 用字符串前向引用）
**步骤**：
1. 新建包 `novacode.command`(`__init__.py` 留空)
2. 定义 `class Kind(Enum)` 与 3 个成员 `LOCAL = "local"`、`UI = "ui"`、`PROMPT = "prompt"`
3. 定义 `Handler = Callable[["CommandContext"], Awaitable[None]]` 类型别名(CommandContext 在 T4 定义,可用前向引用字符串)
4. 定义 `@dataclass(slots=True) class Command` 字段:`name: str`、`description: str`、`kind: Kind`、`handler: Handler`、`aliases: list[str] = field(default_factory=list)`、`hidden: bool = False`

**验证**：`python -c "from novacode.command.command import Kind, Command"` 不报错

### T2: 实现 Registry + 冲突检测 + 前缀匹配
**文件**：`src/novacode/command/registry.py`、`tests/test_command_registry.py`
**依赖**：T1
**步骤**：
1. `class Registry` 含 `_by_name: dict[str, Command]`、`_visible: list[Command]`
2. `__init__`:初始化空 dict + 空 list
3. `register(self, cmd: Command) -> None`:
   - 校验 `cmd.name` 非空且全小写、`cmd.aliases` 全部非空且全小写
   - 遍历 `(cmd.name, *cmd.aliases)` 每个 key,若已存在于 `_by_name` 则 `raise RuntimeError(f"command conflict: {key}")` 含具体冲突键
   - 通过后把每个 key 塞进 `_by_name` 都指向同一个 cmd
   - 若 `not cmd.hidden` 则 append 到 `_visible`,然后对 `_visible` 按 `name` 字典序排序(`_visible.sort(key=lambda c: c.name)`)
4. `lookup(self, name: str) -> Command | None`:小写化 name 后查 `_by_name.get(name.lower())`
5. `visible(self) -> list[Command]`:`return list(self._visible)` (拷贝防外部改动)
6. `prefix_match(self, prefix: str) -> list[Command]`:`p = prefix.lstrip("/").lower()`;遍历 `_visible`,name 以 `p` 开头入选;保持字典序返回;`p == ""` 时返回全部 visible
7. 写 5 个测试:`test_register_ok`、`test_register_duplicate_name_raises`、`test_register_duplicate_alias_raises`、`test_visible_sorted`、`test_prefix_match`

**验证**：`pytest tests/test_command_registry.py -v` 全绿

### T3: parse 输入解析
**文件**：`src/novacode/command/dispatch.py`、`tests/test_command_dispatch.py`
**依赖**：无
**步骤**：
1. `def parse(input_text: str) -> tuple[str, bool]`:对 `input_text` 调 `.strip()`;若不以 `/` 开头返回 `("", False)`;若仅为 `/` 返回 `("", True)`;否则取掉前导 `/`、按 `str.split(maxsplit=1)` 切;若有第二段非空(即用户传了参数),返回 `("", True)`(让 lookup miss);否则返回 `(name.lower(), True)`
2. 表驱动测试样本:`""` / `"   "` / `"hello"` / `"/"` / `"/help"` / `"  /HELP  "` / `"/help xx"`(→ `("", True)`) / `"/help  "`(→ `("help", True)`) / `"//double"` / `"/ /help"`(→ `("", True)`),确认每个返回值

**验证**：`pytest tests/test_command_dispatch.py -v` 全绿

### T4: CommandContext 迁入 command 包并扩展
**文件**：`src/novacode/command/context.py`、`src/novacode/tui/commands.py`
**依赖**：无（声明 CommandContext 让 T1 的 Handler 类型签名合法）
**步骤**：
1. 新建 `command/context.py`,把 `tui/commands.py` 现有的 `CommandContext` dataclass 整体迁入,新增字段:
   - `provider: Any = None`(llm.Provider,model 名数据源)
   - `usage_box: Any = None`(tui.run._UsageBox,usage_in/usage_out 可变容器)
   - 保留 `holder/registry/mode_box/agent/console/exit_flag/run_callback/mem_mgr/runtime/root/sessions_dir` 及 `conv` 只读 property
2. `tui/commands.py` 从 `novacode.command.context` 导入 CommandContext 并 re-export(或直接改 run.py 的 import),删除本地定义
3. `command/__init__.py` re-export `CommandContext`
4. 同步把 `_UsageBox` dataclass 定义在 `tui/run.py`(usage_in/usage_out)

**验证**：`python -c "from novacode.command.context import CommandContext"` 不报错

### T5: 实现 5 条纯本地命令
**文件**：`src/novacode/command/builtin_local.py`
**依赖**：T1、T2、T4
**步骤**：
1. `def make_help_handler(reg: Registry) -> Handler`:返回 `async def _handler(ctx)` 闭包,内部调 `reg.visible()`,计算最长 name 长度做对齐填充,逐条拼 `f"/{c.name.ljust(w)}  {c.description}"`,用 `\n` 连接后 `ctx.console.print(...)` 一次输出
2. `async def handle_status(ctx)`:6 行 key:value,key 列宽固定(`Mode:`/`Tokens:`/`Tools:`/`Memories:`/`Model:`/`Directory:` 中最长那个);值依次:`ctx.mode_box.mode.value`、`f"{ctx.usage_box.usage_in} in / {ctx.usage_box.usage_out} out"`、`f"{ctx.registry.count()} enabled"`、`f"{len(ctx.mem_mgr.list_files())} files"`、`ctx.provider.model`、`ctx.root`;首行加标题 "novaCode Status"(空行隔开)
3. `async def handle_memory(ctx)`:`files = ctx.mem_mgr.list_files()`(注意 list_files 返回 `(project, user)` 两个 list,需拼接;mem_mgr 为 None 时视为空);`if not files: ctx.console.print("无已加载的记忆文件")`;否则按行打印文件名
4. `async def handle_permission(ctx)`:`ctx.console.print(ctx.mode_box.mode.value)`
5. `async def handle_session(ctx)`:`ctx.console.print(f"Session: {ctx.runtime.session.session_id}\nPath: {ctx.holder.writer.path}")`

**验证**：`python -c "import novacode.command.builtin_local"` 通过;后续 T8 的 test_command_builtins 会覆盖

### T6: 实现 5 条影响界面命令
**文件**：`src/novacode/command/builtin_ui.py`
**依赖**：T1、T4
**步骤**：
1. `async def handle_exit(ctx)`: `ctx.exit_flag[0] = True`
2. `async def handle_plan(ctx)`: `ctx.mode_box.mode = Mode.PLAN; ctx.console.print("已进入计划模式（仅只读工具）。输入 /do 开始执行。")`
3. `async def handle_compact(ctx)`: 沿用现有逻辑 — 按 plan 模式选 `read_only_definitions()` / `definitions()`;`await ctx.agent.run_force_compact(ctx.conv, defs)`;打印 token 前后变化;异常时打印"压缩失败"提示(不退出)
4. `async def handle_resume(ctx)`: `from ..tui.resume import do_resume; await do_resume(ctx)`
5. `async def handle_clear(ctx)`: 实现步骤见 plan.md"builtin_ui /clear"一节:close 旧 writer → `new_session_context(ctx.root)` → `session.Writer(new_ses_ctx.session_dir)` + `set_model(ctx.provider.model)` → 重建 `Conversation(on_append=..., on_replace=...)` → 替换 holder.conv/writer/ses_ctx → `ctx.runtime.reset_for_new_session(new_ses_ctx)` → `ctx.usage_box` 归零 → 打印 notice

**验证**：`python -c "import novacode.command.builtin_ui"` 通过

### T7: 实现 2 条提示词命令
**文件**：`src/novacode/command/builtin_prompt.py`
**依赖**：T1、T4
**步骤**：
1. 模块级 `REVIEW_DIRECTIVE = "请审查当前上下文中的代码变更/已读取的文件,指出潜在 bug、可读性问题和可简化处。"`
2. `async def handle_do(ctx)`: `ctx.mode_box.mode = Mode.DEFAULT; ctx.conv.add_user(EXECUTE_DIRECTIVE); await ctx.run_callback()` (import `from novacode.prompt import EXECUTE_DIRECTIVE`)
3. `async def handle_review(ctx)`: `ctx.conv.add_user(REVIEW_DIRECTIVE); await ctx.run_callback()`

**验证**：`python -c "import novacode.command.builtin_prompt"` 通过

### T8: register_builtins + 12 条命令一次性注册
**文件**：`src/novacode/command/builtins.py`、`tests/test_command_builtins.py`
**依赖**：T5、T6、T7
**步骤**：
1. `def register_builtins(reg: Registry) -> None`:按一致顺序注册 12 条 `Command(...)` 字面量(name 全部小写,description 一句中文,kind 按设计,aliases 留默认空 list,hidden=False);`/help` 的 handler 通过 `make_help_handler(reg)` 工厂注入
2. test_command_builtins.py 写:
   - `test_register_builtins_all_registered`(注册后 `reg.visible()` 长度=12、含所有 12 个名字)
   - `test_register_builtins_no_collision`(直接调 register_builtins 不抛)
   - `test_register_builtins_handlers_run_on_fake_ctx`(用 `pytest.mark.asyncio` 把假 CommandContext(SimpleNamespace 填满字段)传给每个命令的 handler,`await` 后断言不抛)
3. 新增可观测桩 `FakeCtx`(简单对象记录 println/error/set_mode/inject 调用);至少 3 个行为断言:
   - `test_handle_status_prints_all_keys` — handle_status 输出文本含 6 个 key(Mode/Tokens/Tools/Memories/Model/Directory)
   - `test_handle_clear_resets_usage` — handle_clear 后 `usage_box` 归零、holder.writer 被替换、runtime.session 被替换
   - `test_handle_do_sets_mode_and_injects` — handle_do 后 `mode_box.mode == Mode.DEFAULT` 且 `conv.add_user` 被调用(EXECUTE_DIRECTIVE)

**验证**：`pytest tests/test_command_builtins.py -v` 全绿;`ruff check src/novacode/command/` 无告警

### T8.5: cli 装配 cmd_registry + run.py 基础设施
**文件**：`src/novacode/cli.py`、`src/novacode/tui/run.py`
**依赖**：T8
**步骤**：
1. cli.py `_amain`:在 `new_default_registry()` 后新增 `cmd_registry = command.Registry(); command.register_builtins(cmd_registry)`;把 `cmd_registry` 传进 `run(...)`(给 `run` 增一个 `cmd_registry` 参数)
2. run.py:定义 `_UsageBox` dataclass(usage_in/usage_out);`run()` 里把 nonlocal `usage_in/usage_out` 改为 `usage_box` 读写(含 run_callback 与主循环两处);`_input_frame` 增 `cmd_registry` 参数
3. 验证启动不被破坏(空壳启动)

**验证**：`python -m novacode` 在合法配置下能启动

### T9: TUI 分发接入 command 包
**文件**：`src/novacode/tui/run.py`、`src/novacode/tui/commands.py`
**依赖**：T8.5
**步骤**：
1. `tui/commands.py`:删除旧 `CommandContext` 定义、`BUILTIN_COMMANDS`、`dispatch_command`、`handle_exit/plan/do/compact/resume/unknown_command`;仅保留 `format_compact_notice`(或整体迁到 run.py)
2. `run.py` 主循环替换分发:
   - `cmd = text.strip()` → `name, is_slash = parse(cmd)`
   - `not is_slash` → 走 `conv.add_user + _stream_reply`(原有逻辑)
   - `is_slash=True`:
     - `c = cmd_registry.lookup(name)`;`c is None` → `console.print("[dim]未知命令: 输入 /help 查看可用命令[/]")`
     - 命中 → `try: await c.handler(ctx) except Exception as e: console.print(error_block(e))`
   - `CommandContext` 构造时补 `provider=provider`、`usage_box=usage_box`
3. 空输入早返回逻辑保留(`if not cmd: continue`)

**验证**：`python -m novacode` 启动后 `/help`、`/status`、`/foobar` 均可触发(肉测);`ruff check .` 无告警

### T10: /resume 收编(注册现有 do_resume)
**文件**：`src/novacode/command/builtin_ui.py`
**依赖**：T6
**步骤**：
1. 无需迁移:ch09 的 `handle_resume → do_resume(ctx)` 已在 T6 注册;`tui/resume.py` 的 `do_resume` / `do_resume_session` / `_pick_session` 保持原样
2. 验证 ch09 的 /resume 行为不受影响(列表展示、↑↓ 选择、Esc 取消、恢复后回看历史)

**验证**：`python -c "from novacode.tui.resume import do_resume"` 通过;肉测 `/resume` 仍弹历史列表

### T11: prompt_toolkit 自动补全
**文件**：`src/novacode/tui/completion.py`(新)
**依赖**：T2
**步骤**：
1. `MAX_ROWS = 8`
2. `class CommandCompleter(Completer)`:
   - `get_completions(document, complete_event)`:文本首字符非 `/` 或含 `\n` → 不产出;否则 `reg.prefix_match(text)` → `yield Completion(text=c.name, start_position=1, display_formatted_text=f"/{c.name.ljust(w)}  {c.description}")`(w 为最长 name 宽)
3. `def bind_completion_keys(kb: KeyBindings) -> None`:
   - `up`/`down`:`buf.complete_state` 非空时 `buf.complete_previous()/complete_next()` 并消费;否则透传(多行光标移动)
   - `enter`:`complete_state` 非空 → `event.app.exit(result="/" + complete_state.current_completion.text)`;否则 `event.app.exit(result=buf.text)`
   - `tab`:`complete_state` 非空 → 同 enter 直接执行;否则无操作(本期不缩进)
   - `escape`:`complete_state` 非空 → `buf.cancel_completion(); event.app.invalidate()`;否则忽略(空闲态 Esc 现状)

**验证**：`python -c "from novacode.tui.completion import CommandCompleter, bind_completion_keys"` 通过

### T12: _input_frame 启用补全
**文件**：`src/novacode/tui/run.py`
**依赖**：T11
**步骤**：
1. `_input_frame` 增 `cmd_registry` 参数;`Buffer(completer=CommandCompleter(cmd_registry), complete_while_typing=True, multiline=True)`
2. 布局:在 BufferControl 的 Window 之后、状态栏 Window 之前插入 `Window(CompletionMenu(max_height=MAX_ROWS), height=8, dont_extend_height=True)`(菜单在无候选时自动零高度)
3. `kb = KeyBindings()` 后调 `bind_completion_keys(kb)`;注意现有 `enter`/`escape` 绑定要与补全分支合并(bind_completion_keys 内覆盖/或统一在 bind 函数处理)

**验证**：`python -m novacode` 启动后键入 `/` 应弹补全菜单(肉测);`ruff check .` 无告警

### T13: run 主循环 + cli 全链路
**文件**：`src/novacode/tui/run.py`、`src/novacode/cli.py`
**依赖**：T12
**步骤**：
1. cli.py 已传 `cmd_registry`(T8.5);确认 `run()` 签名与调用点一致
2. 确认主循环分发(T9)、补全(T12)、`/clear`(T6)、`/status` 等全部走 command 包
3. `pytest tests/test_tui_commands.py -v` 绿(新写分发层测试:大小写不敏感、未知命令、help 输出含 12 名、空输入不分发)

**验证**：`pytest -q` 全绿;`ruff check .` 无告警

### T14: READY_HINT(banner)调整
**文件**：`src/novacode/prompt/__init__.py`、`tests/test_cli.py`(如有 banner 断言)
**依赖**：T13
**步骤**：
1. `render_banner` 就绪行由现有 `"/exit 退出 · /plan 计划模式 · /do 按计划执行 · Shift+Tab 切换权限模式"` 改为 `"就绪：输入 /help 查看可用命令 · Shift+Tab 切换权限模式"`(只提 /help,N7)
2. 检查 test_cli.py / test_prompt.py 是否有该文案断言,有则跟随调整

**验证**：`pytest tests/test_prompt.py tests/test_cli.py -q` 绿

### T15: 端到端验证(tmux 实跑)
**文件**：无(运行可执行文件)
**依赖**：T14
**步骤**：
1. WSL tmux 启动:`tmux new-session -d -s novaspec 'cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode 2>/tmp/novacode.err'`(Windows 本机则为 `.venv\Scripts\python.exe -m novacode`)
2. 按 checklist.md 的"端到端场景(tmux 实跑)"逐项发送按键并截屏:
   - 启动后键入 `/` 看补全菜单是否弹出且含 12 条
   - 键入 `/s` 看是否过滤为 /session、/status
   - 选中 /status 回车验证 6 字段输出
   - 依次跑 /help、/memory、/permission、/session、/review、/clear,逐一观测输出
   - 验证 /plan 切到 plan 模式后状态栏徽章变化
   - 验证 /do 切回 default + 触发 AI 回复
   - 验证 /resume 列表能看到 /clear 之前的旧会话
   - 验证未知命令 /foobar 提示
   - 验证启动期冲突检测:临时给某条命令多注册一遍同名,启动应抛 RuntimeError 退出,看错误信息
3. 全部通过后 `tmux kill-session -t novaspec`

**验证**：按 checklist.md 全部勾选;期间出错则修复后从失败点重跑

## 执行顺序

```
T0a, T0b, T0c (并行) → T1 → (T2, T3, T4 并行) → (T5, T6, T7 并行) → T8 → T8.5 → T9 → T10 → T11 → T12 → T13 → T14 → T15
```

- T0a/T0b/T0c 是底层 helper 铺垫(memory.list_files、session.Writer.path、runtime.reset_for_new_session、tool.Registry.count),互不依赖可并行
- T1/T2/T3/T4 是 command 包基础,T1→T2 是结构依赖,T2/T3/T4 互不依赖可并行
- T5/T6/T7 三组命令实现互不依赖,可并行
- T8 必须在 T5+T6+T7 后
- T8.5 给 cli/run 搭基础设施,作为 T9 前置
- T9 把主循环分发接入 command 包;T10 确认 /resume 收编;T11(补全)仅依赖 T2,可与 T9 并行
- T12 把补全接进 _input_frame;T13 全链路测试;T14 是 banner 文案;T15 是端到端验证
~~~
