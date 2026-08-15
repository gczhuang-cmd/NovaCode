~~~Markdown
# slash命令体系 Plan

## 架构概览

```
┌──────────────────────────────────────────────────────────┐
│                  src/novacode/tui                         │
│                                                          │
│  run() 主循环 ─┬─► command.parse → command.Registry       │
│               │       (lookup by name/alias)             │
│               │             │                            │
│               │             ▼                            │
│               │      command.Command.handler(ctx)        │
│               │             │                            │
│               │             ▼ via command.CommandContext │
│               │      holder/agent/console/mode_box/…     │
│               │                                          │
│               └─► (非 / 开头) conv.add_user + _stream_reply│
│                                                          │
│  _input_frame (ptk) ─► Buffer(completer=CommandCompleter,│
│                        complete_while_typing)            │
│                        + CompletionMenu(max_height=8)    │
└──────────────────────────────────────────────────────────┘
                  ▲
                  │ 依赖
                  │
┌──────────────────────────────────────────────────────────┐
│            src/novacode/command  (新包)                   │
│                                                          │
│  Kind/Command/Handler 类型定义                           │
│  CommandContext 从 tui/commands.py 迁入并扩展            │
│  Registry: 注册 + 冲突检测 + 前缀匹配 + 字典序排序       │
│  parse: 解析 /<name> 形态                                │
│  12 条内置命令的 handler 实现 (按 Kind 分文件)            │
└──────────────────────────────────────────────────────────┘
```

- 新建包 `src/novacode/command/`：纯领域逻辑，不依赖 prompt_toolkit / Rich 具体类型（`CommandContext` 引用的 `Console`/`Agent` 等是 novacode 层的既有类型，不属于 tui）
- 既有包 `src/novacode/tui/`：`commands.py` 删掉旧注册表与 5 个旧 handler，仅保留 `format_compact_notice`（供 `_stream_reply` 用）；`run.py` 主循环改为从 `command` 包查 Registry 分发
- 自动补全菜单用 **prompt_toolkit 原生 completer + `CompletionMenu` 控件**实现，完全在 `src/novacode/tui/completion.py` 中构造，只读 Registry 拿候选列表

> 修订说明：原文档以 Textual（`novaCodeApp`/`RichLog`/`Static` widget）为蓝本。本项目 TUI 是 prompt_toolkit 非全屏 + Rich console 直写 scrollback，`tui/` 下不存在 `app.py`/`stream.py`/`complete.py`，命令分发在 `run()` 主循环。以下所有模块设计均已按实际结构重写。

## 核心数据结构
### `command.Kind`

```python
from enum import Enum

class Kind(Enum):
    LOCAL = "local"     # 纯本地: 只打印, 不改 App, 不进 history
    UI = "ui"           # 影响界面: 改会话/模式/进程状态, 不进 history
    PROMPT = "prompt"   # 提示词: 注入 user 消息 + 触发回合, 进 history
```

### `command.Command`

```python
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .context import CommandContext

Handler = Callable[[CommandContext], Awaitable[None]]

@dataclass(slots=True)
class Command:
    name: str                                # 不带 "/" 前缀, 全小写, 唯一
    description: str                         # 一句话, 用于 /help 与补全菜单
    kind: Kind
    handler: Handler
    aliases: list[str] = field(default_factory=list)  # 不带 "/" 前缀, 全小写, 全局唯一(含 name)
    hidden: bool = False                     # /help 与补全菜单都不显示, 但 dispatcher 仍可命中
```

### `command.Registry`

```python
class Registry:
    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}   # 主名 + 别名都映射到同一 Command, key 已转小写
        self._visible: list[Command] = []        # 按 name 字典序, 排除 hidden

    def register(self, cmd: Command) -> None:   # 名/别名冲突即 raise RuntimeError
    def lookup(self, name: str) -> Command | None:   # name 已是小写
    def visible(self) -> list[Command]:               # 返回已排序的可见命令副本
    def prefix_match(self, prefix: str) -> list[Command]:  # prefix 含 "/", 内部 strip 并小写; 前缀匹配 name; 不匹配别名/描述
```

### `command.CommandContext`（从 `tui/commands.py` 迁入并扩展）

原 `CommandContext` 定义在 `tui/commands.py`，已具备 holder/registry/mode_box/agent/console/exit_flag/run_callback/mem_mgr/runtime/root/sessions_dir + `conv` 只读 property。ch10 迁入 command 包并新增只读查询字段，handler 通过它访问 TUI 能力（G3）：

```python
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

@dataclass
class CommandContext:
    holder: Any                 # tui.run._SessionHolder（含 conv/writer/ses_ctx）
    registry: Any               # tool.Registry（工具注册中心）
    mode_box: Any               # tui.run._ModeBox（duck typing：.mode）
    agent: Any                  # Agent（run_force_compact / read_only_definitions 等）
    console: Any                # rich.console.Console（println/error 输出）
    exit_flag: list[bool]       # /exit 置 True, run 主循环据此 break
    run_callback: Callable[[], Awaitable[None]]  # /do /review 触发立即回合
    provider: Any               # llm.Provider（model 名数据源）
    usage_box: Any              # tui.run._UsageBox（usage_in/usage_out 可变容器）
    mem_mgr: Any = None         # memory.Manager（ch09, 异步记忆更新）
    runtime: Any = None         # agent.SessionRuntime（会话上下文）
    root: str = ""              # 项目根目录
    sessions_dir: str = ""      # .novacode/sessions 目录

    @property
    def conv(self) -> Any:
        """当前会话历史（读 holder 中最新引用, do_resume//clear 后自动更新）。"""
        return self.holder.conv
```

新增字段说明：
- `provider`：run() 选定 provider 后注入；`model_name()` 查询用 `ctx.provider.model`。
- `usage_box`：`_UsageBox(usage_in, usage_out)` dataclass，替换 run() 里原来的 nonlocal 变量，/clear 归零与 /status 读取共用同一可变容器。

**查询能力映射**（对应原 Textual UI Protocol 的各方法）：

| 原 UI Protocol 方法 | 实际数据源 |
|---|---|
| `println(msg)` / `error(msg)` | `ctx.console.print(...)`（Rich markup） |
| `mode()` / `set_mode(m)` | `ctx.mode_box.mode` |
| `inject_and_send(label, preset)` | `ctx.conv.add_user(preset)` + `await ctx.run_callback()` |
| `usage_in()` / `usage_out()` | `ctx.usage_box.usage_in` / `usage_box.usage_out` |
| `model_name()` | `ctx.provider.model` |
| `cwd()` | `ctx.root` |
| `tool_count()` | `ctx.registry.count()`（T0c 新增） |
| `memory_files()` | `ctx.mem_mgr.list_files()`（T0a 新增；mem_mgr 为 None 时返回空） |
| `session_path()` | `ctx.holder.writer.path`（T0b 新增；writer 为 None 时返回 ""） |
| `session_id()` | `ctx.runtime.session.session_id`（runtime 为 None 时返回 ""） |
| `quit()` | `ctx.exit_flag[0] = True` |
| `force_compact()` | `ctx.agent.run_force_compact(...)` |
| `open_resume_menu()` | 直接调 `tui.resume.do_resume(ctx)` |
| `clear_and_new_session()` | 见 builtin_ui `/clear` 小节 |
| `idle()` | 命令分发只在空闲阶段发生，无需查询（N3a） |

### `tui.CompletionMenu`（prompt_toolkit 原生方案）

不自定义渲染，使用 prompt_toolkit 内置补全能力：

```python
# tui/completion.py
from prompt_toolkit.completion import Completer, Completion

MAX_ROWS = 8

class CommandCompleter(Completer):
    """按输入框文本前缀匹配注册命令名（F24/F25/F32a）。"""
    def __init__(self, reg): self._reg = reg
    def get_completions(self, document, complete_event):
        text = document.text
        if not text.startswith("/") or "\n" in text:
            return          # 首字符非 "/" 或含换行 → 无候选（F26/F32a）
        for c in self._reg.prefix_match(text):
            display = f"/{c.name.ljust(<最长name宽>)}  {c.description}"
            yield Completion(text=c.name, start_position=1,
                             display_formatted_text=display)
```

- `Buffer(completer=CommandCompleter(reg), complete_while_typing=True)` → 输入实时刷新菜单、零匹配无菜单、删空自动关闭（F26/F32b）。
- 布局在 BufferControl 下方、状态栏上方插 `CompletionMenu(max_height=MAX_ROWS)`，紧贴输入框（F5/F6/N5）。
- 键位（自定义 KeyBindings）：
  - ↑/↓：补全激活时导航高亮（complete_next/previous）；否则回退光标移动
    cursor_up/down——避免默认 `auto_down` 在无补全最后一行触发 history_forward（F29/F32）
  - Enter：补全激活且**有选中项**（complete_index 非 None）时以 `"/"+name` 提交执行；
    无选中项时提交输入框原文走正常分发（F30/F32b）
  - Tab：同 Enter（补全激活且有选中项时执行）（F30）
  - ESC：有 `complete_state` 时 `buffer.cancel_completion()` 关闭菜单、文本保留；空闲态忽略（F31）
  - 补全菜单初始**无高亮**（complete_index=None），第一次 ↓ 才选中第一个候选
- `mouse_support=False` → 无鼠标点击（F32c）。

## 模块设计
### `src/novacode/command/__init__.py`
**职责**：包出口，re-export `Kind` / `Command` / `Handler` / `Registry` / `parse` / `register_builtins` / `CommandContext`。

### `src/novacode/command/command.py`
**职责**：定义 `Kind`、`Command`、`Handler` 类型别名。
**对外接口**：上面列出的类型。
**依赖**：仅标准库 `dataclasses`、`enum`、`typing` + `context.py`（Handler 引用 CommandContext）。

### `src/novacode/command/context.py`
**职责**：`CommandContext`（从 `tui/commands.py` 迁入，按上文扩展字段）。
**对外接口**：`CommandContext`。
**依赖**：仅标准库 `dataclasses`/`typing`；字段类型用 duck typing（Any 或 novacode 层既有类型），不 import tui。

### `src/novacode/command/registry.py`
**职责**：注册中心。维护 `_by_name` 字典、`_visible` 排序列表；`register` 时做冲突检测;`prefix_match` 提供补全数据源。
**对外接口**：上面 `Registry` 的方法集。
**依赖**：仅 `command.py` + 标准库。

### `src/novacode/command/dispatch.py`
**职责**：`parse(input_text: str) -> tuple[str, bool]` —— 空白/空串/非 `/` 开头返回 `("", False)`;只含 `/` 返回 `("", True)`;取掉前导 `/`、第一个空白前的部分小写化作为 name;若 name 之后还有非空白尾随字符(用户传了参数),返回 `("", True)` 让 `lookup` 必然 miss 走未命中分支。纯字符串操作,无副作用。Registry 上的 `lookup` 已能完成查找,dispatch 不必另外封装。
**对外接口**：`parse`。

### `src/novacode/command/builtin_local.py`
**职责**：5 条纯本地命令的 handler——`/help`、`/status`、`/memory`、`/permission`、`/session`。
- `/help`：闭包捕获 reg，调用 `reg.visible()`，按"<name>  <description>" 两列对齐输出，通过 `ui.println` 打印。`register_builtins` 把 reg 自身传入 help handler 工厂。
- `/status`：按固定顺序输出 6 行——`Mode/Tokens/Tools/Memories/Model/Directory`，值来自 `ctx.mode_box.mode.value` / `ctx.usage_box.usage_in`/`usage_out` / `ctx.registry.count()` / `len(ctx.mem_mgr.list_files())` / `ctx.provider.model` / `ctx.root`。
- `/memory`：调用 `ctx.mem_mgr.list_files()`，逐行打印文件名；为空时打印"无已加载的记忆文件"。
- `/permission`：打印 `ctx.mode_box.mode.value` 一行。
- `/session`：打印 "Session: <id>" + "Path: <path>" 两行(值来自 `ctx.runtime.session.session_id`、`ctx.holder.writer.path`)。

### `src/novacode/command/builtin_ui.py`
**职责**：5 条影响界面命令——`/exit`、`/plan`、`/compact`、`/resume`、`/clear`。
- `/exit`：`ctx.exit_flag[0] = True`。
- `/plan`：`ctx.mode_box.mode = Mode.PLAN` + `ctx.console.print("已进入计划模式...")`（沿用现有文案）。
- `/compact`：`await ctx.agent.run_force_compact(ctx.conv, defs)` + 打印 token 前后变化（沿用现有 handler 逻辑；idle 守护天然满足，无需检查）。
- `/resume`：`await do_resume(ctx)`（tui.resume 现有函数，ch09 行为不变）。
- `/clear`：见下。

**`/clear` 实现步骤**：
1. `old_writer = ctx.holder.writer`；若非 None → `old_writer.close()`（释放句柄）。
2. `new_ses_ctx = new_session_context(ctx.root)`（compact 包，建好 `.novacode/sessions/<id>/tool-results`）。
3. `new_writer = session.Writer(new_ses_ctx.session_dir)`；`new_writer.set_model(ctx.provider.model)`。
4. `ctx.holder.writer = new_writer`；重建 `new_conv = Conversation(on_append=new_writer.on_append, on_replace=new_writer.on_replace)`；`ctx.holder.conv = new_conv`。
5. `ctx.holder.ses_ctx = new_ses_ctx`；`ctx.runtime.reset_for_new_session(new_ses_ctx)`（T0c）。
6. `ctx.usage_box.usage_in = 0; ctx.usage_box.usage_out = 0`。
7. 输出 notice（明确告知已切换会话，避免界面看起来无变化）：`ctx.console.print(f"已结束当前会话（{旧session_id}），开启新会话（{新session_id}）。旧会话可用 /resume 恢复。")`。终端 scrollback 不编程清除（spec 修订：scrollback 属终端历史）。

旧会话目录与 JSONL 保留在磁盘，/resume 仍能看到（N9）。

### `src/novacode/command/builtin_prompt.py`
**职责**：2 条提示词命令——`/do`、`/review`。
- `/do`：`ctx.mode_box.mode = Mode.DEFAULT` + `ctx.conv.add_user(EXECUTE_DIRECTIVE)` + `await ctx.run_callback()`（沿用现有 handler）。
- `/review`：`ctx.conv.add_user(REVIEW_DIRECTIVE)` + `await ctx.run_callback()` (REVIEW_DIRECTIVE 是模块级常量，文案如 "请审查当前上下文中的代码变更,指出潜在 bug、可读性问题、可简化处")。

### `src/novacode/command/builtins.py`
**职责**：`register_builtins(reg: Registry) -> None`——按一致顺序在 reg 上注册 12 条命令，把对应 handler 写进 `Command(...)` 构造里；`/help` 的 handler 需要通过工厂函数捕获 reg。
**对外接口**：`register_builtins(reg)`。

### `src/novacode/tui/commands.py` (改造)
**职责**：变成薄文件：
1. 删除旧 `CommandContext`、`BUILTIN_COMMANDS`、`handle_exit/plan/do/compact/resume/unknown_command`、`dispatch_command`；
2. 仅保留 `format_compact_notice(ev)`（`_stream_reply` 仍在用）——或迁至 `tui/run.py`；
3. 其余命令逻辑已迁入 `novacode/command/` 包。

### `src/novacode/agent/runtime.py` (改动)
- `SessionRuntime` 新增 `reset_for_new_session(self, ses_ctx: SessionContext) -> None` 方法:原子重置 `replacement` / `recovery` / `auto_tracking` 三个 compact 子状态，`usage_anchor` / `anchor_msg_len` / `turn_count` 清零，`session` 字段指向新的 `ses_ctx`;`context_window` 保留;writer 与 conv 重建由 `/clear` handler 自身负责,不进 runtime 接口。

### `src/novacode/tui/completion.py` (新)
**职责**：自动补全的 prompt_toolkit 装配。
- `CommandCompleter(Completer)`：见上文"tui.CompletionMenu"一节。
- `bind_completion_keys(kb: KeyBindings)`：给传入的 KeyBindings 加 ↑/↓/Enter/Tab/ESC 的补全分支（仅 `complete_state` 非空时消费，否则透传/走默认）。
- 供 `_input_frame` 使用：`Buffer(completer=CommandCompleter(reg), complete_while_typing=True, multiline=True)` + layout 中在输入框下方加 `CompletionMenu(max_height=MAX_ROWS)`。

### `src/novacode/tui/run.py` (改动)
- `_SessionHolder` 保留；新增 `_UsageBox` dataclass（usage_in/usage_out）。
- `run()` 主循环：
  - 构造 `cmd_registry = Registry(); register_builtins(cmd_registry)`（或由 cli 注入，见下"启动装配"）；
  - `_input_frame` 传入 `cmd_registry`，启用补全；
  - 提交文本后：`name, is_slash = parse(cmd)`；`is_slash=True` 时查 `cmd_registry.lookup(name)`，命中则 `await cmd.handler(ctx)`（try/except 包一层，异常打印 error_block），未命中打印"未知命令: 输入 /help 查看可用命令"；`is_slash=False` 走 `conv.add_user + _stream_reply`；
  - `CommandContext` 构造处补 `provider=provider`、`usage_box=usage_box`；
  - `usage_in/usage_out` nonlocal 改为 `usage_box` 读写。

### 启动装配（`src/novacode/cli.py` 改动）
- `_amain` 在构造 `new_default_registry()` 后新增：`cmd_registry = command.Registry(); command.register_builtins(cmd_registry)`；把 `cmd_registry` 传入 `run(...)`。
- 冲突检测在 cli 装配期触发：`register_builtins` 内 `reg.register` 抛 `RuntimeError` → `main()` 的 `except Exception` 兜底打印"启动失败: command conflict: <name>"并以退出码 1 结束（F2/AC16）。

### `src/novacode/prompt/__init__.py` (改动)
- `render_banner` 的就绪行文案由 `"/exit 退出 · /plan 计划模式 · /do 按计划执行 · Shift+Tab 切换权限模式"` 改为 `"已就绪，输入 /help 查看可用命令 · Shift+Tab 切换权限模式"`（N7：命令名只允许出现 /help）。

## 模块交互
### 命令分发流（用户回车）

```
keystroke (Enter) ─► tui.run._input_frame (prompt_toolkit Application)
                       │  exit(result=text)；补全激活时 result="/"+选中候选
                       ▼
                 run() 主循环
                  strip 输入
                  空输入 / None → 早返回
                       │
                       ▼
                 name, is_slash = command.parse(text)
                  │
                  ├─ is_slash=False → conv.add_user(text) + _stream_reply
                  │
                  └─ is_slash=True
                       ├─ cmd_registry.lookup(name)
                       │   未找到 → console.print(notice 未知命令提示), 继续
                       │
                       └─ await cmd.handler(ctx)
                            │
                            ├─ 抛异常 → console.print(error_block(str(exc)))
                            │
                            ▼
                          通过 CommandContext 操作
                            ├─ println    → ctx.console.print(...)
                            ├─ set_mode   → ctx.mode_box.mode = new_mode
                            ├─ inject_and_send → ctx.conv.add_user(preset) + await ctx.run_callback()
                            ├─ quit       → ctx.exit_flag[0] = True
                            ├─ force_compact / do_resume / clear_and_new_session → 对应 sub-flow
```

handler 是 async 函数，主循环直接 `await` 即可（asyncio 单线程）。

### 自动补全流

```
keystroke (任意字符) ─► _input_frame 的 Buffer（complete_while_typing=True）
                          │
                          ▼
                  Buffer 内部调用 CommandCompleter.get_completions(document)
                    首字符非 "/" 或含换行 → 无候选（菜单消失）
                    否则 reg.prefix_match(text) → Completion 流
                          │
                          ▼
                  CompletionMenu(max_height=8) 渲染候选（紧贴输入框下方）
                          │
                  ↑/↓ → 导航高亮（complete_state 非空时）
                  Enter → exit(result="/"+选中候选)  → 主循环直接执行命令
                  Tab  → 同上（补全激活时）
                  ESC  → buffer.cancel_completion()（菜单关闭, 文本保留）
```

## 文件组织

```
src/novacode/command/                  新包
├── __init__.py        re-export
├── command.py         Kind 枚举, Command 与 Handler 类型
├── context.py         CommandContext（从 tui/commands.py 迁入并扩展）
├── registry.py        Registry: register/lookup/visible/prefix_match
├── dispatch.py        parse(input_text)
├── builtins.py        register_builtins(reg) + REVIEW_DIRECTIVE 常量
├── builtin_local.py   /help /status /memory /permission /session
├── builtin_ui.py      /exit /plan /compact /resume /clear
└── builtin_prompt.py  /do /review

tests/
├── test_command_registry.py   注册中心冲突 / 前缀匹配测试
├── test_command_dispatch.py   parse 测试
└── test_command_builtins.py   12 条命令的注册与假 ctx 调用测试

src/novacode/tui/
├── commands.py        改造: 仅保留 format_compact_notice（或并入 run.py）; 删除旧注册表/handler
├── completion.py      新: CommandCompleter + bind_completion_keys
├── run.py             改: 主循环接入 cmd_registry/usage_box/补全; _input_frame 启用补全
├── resume.py          不变: do_resume/do_resume_session 沿用, 注册即可
├── view.py            不变: 状态栏/mode 徽章保持现状
├── select.py          不变

src/novacode/agent/runtime.py  改: 新增 SessionRuntime.reset_for_new_session(ses_ctx)
src/novacode/memory/manager.py 改: 新增 list_files()
src/novacode/session/writer.py 改: 新增 path 属性
src/novacode/tool/__init__.py  改: Registry 新增 count()
src/novacode/cli.py            改: 构造 cmd_registry + register_builtins, 传入 run()
src/novacode/prompt/__init__.py 改: render_banner 就绪行只保留 /help 引导
src/novacode/__main__.py       不变
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 命令系统包归属 | 新建 `src/novacode/command/`,不留在 `src/novacode/tui/` | tui 内 handler 持有具体上下文紧耦合，要满足 G3 必须把命令逻辑搬出 tui 包 |
| UI 抽象 | 复用/扩展 `CommandContext`,不新建 Textual UI Protocol | 本项目无 App 类可解耦；CommandContext 已屏蔽 prompt_toolkit/Rich 细节，迁入 command 包即满足 G3 |
| 注册方式 | 显式 `register_builtins(reg)`,不用 import 副作用 | 测试时能用空 registry,启动顺序明确,易做单测 |
| 冲突检测 | `register` 内部 `raise RuntimeError`,信息含具体名字/别名 | 失败快,启动期就报,不会进入运行时 |
| Handler 函数签名 | `async def handler(ctx: CommandContext) -> None` | 满足 G3 解耦;handler 抛异常时,主循环 `try/except` 打印 error_block,用户能看到失败 |
| Idle 守护 | 不引入 `SessionState` 状态机 | 命令分发只发生在主循环空闲阶段（流式期间无输入框），天然互斥（N3a） |
| /clear 清屏 | 不编程清除终端 scrollback | 非全屏 Rich 输出属终端历史，应用层无法清除；语义为开新会话 + 状态归零 |
| 补全菜单实现 | prompt_toolkit 原生 `Buffer(completer=...)` + `CompletionMenu` 控件 | 原生已处理候选刷新/高亮/滚动/零匹配关闭，稳定省力；不抢焦点 |
| 补全菜单激活条件 | TextArea/Buffer 首字符为 `/` | 简单可靠;空输入或非 "/" 开头都不弹 |
| 补全键位 | complete_state 非空时 ↑/↓/Enter/Tab/ESC 被消费;关闭时透传 | 用户在菜单激活时不会期望普通编辑;关闭后所有键回到输入框 |
| 命令名大小写 | `parse` 小写化 + Registry key 小写 | 满足 F4 |
| 老命令收编 | 一次性把 5 条旧 handler 重写为基于 CommandContext 的新 handler;不保留过渡 | 双轨维护成本高于一次性重写 |
| /resume 收编 | 直接注册现有 `do_resume` | ch09 行为完全保留（N10） |
| /status 数据源 | `ctx.registry.count()` / `ctx.mem_mgr.list_files()` / `ctx.provider.model` / `ctx.usage_box` | 各取真实数据源，不读持久化配置 |
| 未命中提示文本 | "未知命令: 输入 /help 查看可用命令" | 唯一硬编码字符串;集中在 tui 分发处 |
| 启动横幅 | `render_banner` 就绪行改为只提 /help | 消除 N7 要求的"硬编码命令清单" |
| 状态栏改动 | 不动 | N11 要求 |
~~~

