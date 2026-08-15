# Worktree 隔离 Plan

## 架构概览

新建 `src/novacode/worktree/` 子包，集中放 Manager、Worktree、WorktreeSession、Slug 校验、创建后设置、自动清理、过期清理。其余包按以下方式接入：

- **`novacode.tool`**：新增 `ctx.py`（with_cwd / cwd_from_ctx / resolve_path，项目首次引入 ContextVar）；改造 6 个核心工具用 resolve_path（`glob_tool.py` / `grep_tool.py`）
- **`novacode.subagent`**：`Definition` 加 `isolation` 字段，`parser.py` 解析 `isolation:` frontmatter
- **`novacode.agent`**：`AgentTool.execute` 加 `_execute_with_worktree` 分支，启动时通过 ctx 注入 cwd
- **`novacode.command`**：新增 `builtin_worktree.py`（`/worktree` 一级命令 + 子命令 create/list/enter/exit/remove）；`Command` 加 `args_handler` 字段；`dispatch.parse` 改三元组 `(name, tail, is_slash)`；`dispatch_slash` 命中后透传 `tail`；`CommandContext` 加 `worktree_mgr` / `set_active_cwd` 字段。**不新建 `command/ui.py`（ch10 已废弃 UI Protocol）**
- **`novacode.tui`**：主循环在 `tui/run.py` 的 `run()`（无 `app.py` / `novaCodeApp`）。`run()` 加 `worktree_mgr` 参数，内部维护 `active_cwd`（可变容器，空=进程 cwd）；每次主 Agent Run（`_stream_reply`）前用 `with_cwd(active_cwd)` 包住；CommandContext 注入 `worktree_mgr` 与 `set_active_cwd` 回调
- **`src/novacode/cli.py`**：`Manager(root)` 落在 `subagent_catalog = load_subagent_catalog(root)` 之后；失败降级为 None（stderr 警告）；把 Manager 传给 `AgentTool` 与 `run()`
- **`.gitignore`**：追加 `.novacode/worktrees/` 与 `.novacode/worktree_session.json`

## 核心数据结构

### worktree.Worktree

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Worktree:
    name: str                # 原始 slug(可能含 /)
    path: str                # 绝对路径
    branch: str              # worktree-<flat_slug>
    based_on: str            # 创建时的 base 引用(HEAD / SHA)
    head_commit: str         # 创建时的 commit SHA
    created: datetime
    manual: bool             # True=用户手动创建(/worktree create 路径)
```

### worktree.WorktreeSession

```python
from dataclasses import dataclass, asdict
import json

@dataclass
class WorktreeSession:
    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str
    hook_based: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "WorktreeSession":
        return cls(**json.loads(raw))
```

### worktree.Manager

```python
import asyncio
from pathlib import Path

class Manager:
    def __init__(self, repo_root: str) -> None: ...
    # repo_root: 绝对路径
    # worktree_dir: <repo_root>/.novacode/worktrees
    # session_file: <repo_root>/.novacode/worktree_session.json
    # symlink_dirs: 默认 ["node_modules", ".venv", "vendor"]
    # lock: asyncio.Lock
    # active: dict[str, Worktree]
    # current_session: WorktreeSession | None

    async def create(self, name: str, base_ref: str, manual: bool) -> Worktree: ...
    async def enter(self, name: str) -> WorktreeSession: ...
    async def exit(self, name: str, action: "ExitAction", opts: "ExitOptions") -> "ExitReport": ...
    async def remove(self, name: str, opts: "ExitOptions") -> None: ...
    async def auto_cleanup(self, name: str) -> "AutoCleanupReport": ...
    async def sweep_stale(self, cutoff: "datetime") -> list[str]: ...
    def list(self) -> list[Worktree]: ...
    def get(self, name: str) -> Worktree | None: ...
    def current_session(self) -> WorktreeSession | None: ...
```

### worktree 辅助类型

```python
from enum import Enum

class ExitAction(str, Enum):
    KEEP = "keep"
    REMOVE = "remove"

@dataclass
class ExitOptions:
    discard_changes: bool = False

@dataclass
class ExitReport:
    removed: bool
    path: str
    branch: str

@dataclass
class AutoCleanupReport:
    kept: bool
    path: str = ""
    branch: str = ""

class WorktreeHasChangesError(Exception):
    """Worktree 有未提交修改或本地多于 base 的 commit。"""
```

### tool ctx 帮助函数

```python
# src/novacode/tool/ctx.py
import contextvars
from contextlib import contextmanager
from pathlib import Path

_ctx_cwd: contextvars.ContextVar[str | None] = contextvars.ContextVar("cwd", default=None)

@contextmanager
def with_cwd(directory: str):
    if not directory:
        yield
        return
    token = _ctx_cwd.set(directory)
    try:
        yield
    finally:
        _ctx_cwd.reset(token)

def cwd_from_ctx() -> str | None:
    return _ctx_cwd.get()

def resolve_path(p: str) -> str:
    base = _ctx_cwd.get() or str(Path.cwd())
    if not p:
        return base
    pp = Path(p)
    if pp.is_absolute():
        return str(pp)
    return str(Path(base) / pp)
```

### subagent.Definition 扩展

```python
@dataclass
class Definition:
    # ... 既有字段（tools/disallowed_tools/model/max_turns/permission_mode/dont_ask/background...）...
    isolation: str = ""  # "" 或 "worktree"
```

## 模块设计

### `novacode.worktree`（新子包）

**职责:** Worktree 完整生命周期管理 + Slug 校验 + 后台清理。
**对外接口:** Manager(含上面所列方法)+ validate_slug + WorktreeHasChangesError 等导出常量/类型。
**依赖:** 标准库 + `asyncio.create_subprocess_exec` 调 git。不依赖 agent / command / tui（无导入循环）。

**关键内部函数:**
- `validate_slug(name: str) -> None` (失败抛 ValueError)
- `flat_slug(name: str) -> str` (`/` → `+`)
- `_perform_post_creation_setup(repo_root, wt_path, symlink_dirs)`
- `_has_worktree_changes(wt_path, base_commit) -> bool` (fail-closed)
- `_resolve_head_sha_from_fs(wt_path) -> str | None` (快速恢复，纯 fs)
- `_read_worktree_include(repo_root) -> list[str]`
- `_list_ignored_files(repo_root) -> list[str]`
- `_run_git(work_dir, *args) -> str` (统一 env: `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=""`，stdin 关闭)
- `random_agent_name() -> str` (用于 SubAgent 临时 worktree 名)

**文件:**
- `__init__.py` — 公开导出 Manager / validate_slug / WorktreeHasChangesError 等
- `manager.py` — Manager 类型 + 主要方法骨架
- `create.py` — Create + 快速恢复 + 创建后设置
- `lifecycle.py` — enter / exit / remove / auto_cleanup
- `sweep.py` — sweep_stale
- `slug.py` — validate_slug + flat_slug
- `session.py` — WorktreeSession + 持久化(JSON 原子写)
- `git.py` — `_run_git` helper、`_resolve_head_sha_from_fs`、`_has_worktree_changes`
- 测试统一在 `tests/test_worktree_*.py`

### `novacode.tool` 改造

**职责:** 增加 ctx cwd 传递机制（项目首次引入 ContextVar），改造 6 个工具用 resolve_path / 子进程 cwd 参数。
**对外接口:** with_cwd / cwd_from_ctx / resolve_path(新增)；6 个工具 execute 行为变更但 schema 不变。
**依赖:** 无新增。

**文件改动:**
- `ctx.py` — 新增
- `read_file.py` / `write_file.py` / `edit_file.py` — 在 `Path(...).stat()` / `read_text` / `write_text` 前用 `resolve_path(args.path)`
- `glob_tool.py` — 用 `resolve_path(args.path)`（既有参数名是 `path`）
- `grep_tool.py` — 同 glob_tool
- `bash.py` — `asyncio.create_subprocess_shell(..., cwd=resolve_path(""))`（空串解析为 ctx cwd 或进程 cwd）

### `novacode.subagent` 改造

**职责:** Definition 加 isolation 字段；parser 解析。
**改动:**
- `parser.py` — frontmatter 字典中读 `isolation` 字段（小写键，与 camelCase 的 permissionMode/disallowedTools 不同），合法值 `""` / `"worktree"`，其他值 stderr 警告回落空
- `definition.py` — `Definition` 加 `isolation: str = ""`

### `novacode.agent` 改造

**职责:** AgentTool 增加 worktree 分支，接受 Manager。
**改动:**
- `agent_tool.py`:
  - `AgentTool` 加属性 `worktree_mgr: worktree.Manager | None`
  - `AgentTool.__init__(catalog, task_mgr, parent=None, bg_enabled=True, worktree_mgr=None)`（签名末尾追加）
  - `execute` 内 `definition.isolation == "worktree"` 时走 `self._execute_with_worktree(...)`；**且强制前台**——计算 `background` 后若 `isolation == "worktree"` 直接置 `background = False`（忽略 `background` 字段与 `run_in_background`）
- 新增 `agent_worktree.py`:
  - `_execute_with_worktree(manager: Manager, definition, sub_agent, sub_conv, prompt: str, events) -> str`(async)
  - `build_worktree_notice(parent_cwd: str, wt_path: str) -> str`
  - 直接 `from novacode.worktree import Manager, random_agent_name`（worktree 包不依赖 agent，无导入循环）

### `novacode.command` 新增

**职责:** `/worktree` 一级命令 + 子命令解析 + 带参数命令机制。
**改动:**
- `command.py` — `Command` 加可选字段 `args_handler: Callable[[CommandContext, str], Awaitable[None]] | None = None`
- `dispatch.py` — `parse()` 改返回三元组 `(name, tail, is_slash)`（`/help xx` 返回 `("help", "xx", True)`，ch14 起支持带参数命令）；`dispatch_slash` 命中命令后 `cmd.args_handler` 非空则调 `args_handler(ctx, tail)`，否则 `cmd.handler(ctx)`
- `context.py` — `CommandContext` 加 `worktree_mgr: Any = None`、`set_active_cwd: Any = None`（duck typing，command 包不 import worktree / tui）
- `builtins.py` — 注册 `Command(name="worktree", kind=Kind.UI, args_handler=handle_worktree)`（`handler` 可省略或用 no-op，args_handler 优先）
- 新增 `builtin_worktree.py`（handler 内自己 split 子命令 + 参数，用 `ctx.worktree_mgr` 与 `ctx.console.print`）

**CommandContext 扩展（新增字段）:**

```python
@dataclass
class CommandContext:
    # ... 既有字段 ...
    worktree_mgr: Any = None   # worktree.Manager | None（ch14：/worktree 命令访问）
    set_active_cwd: Any = None  # Callable[[str], None] | None（ch14：/worktree enter/exit 更新主循环 active_cwd）
```

### `novacode.tui` 改造

**职责:** 持有 Manager 引用，把 active_cwd 注入主 Agent ctx。
**改动（`tui/run.py`，无 app.py / novaCodeApp）:**
- `run()` 加参数 `worktree_mgr=None`（ch14）
- 主循环内维护 `active_cwd_box: list[str] = [""]`（空表示进程 cwd），提供 `_set_active_cwd(cwd)` 写它、`_effective_cwd()` 读它（空则 `os.getcwd()`）
- 构造 CommandContext 时注入 `worktree_mgr` 与 `set_active_cwd=_set_active_cwd`
- 主 Agent 每次 Run（`_stream_reply` 调用点，含 `/do`/`/review` 的 `run_callback`）前用 `with with_cwd(_effective_cwd()):` 包住
- 启动装配：若 `worktree_mgr` 非 None 且 `manager.current_session()` 非空，初始化 `active_cwd_box[0] = session.worktree_path`

```python
async def _stream_reply_with_cwd(agent, conv, console, mode, cancel, cwd: str):
    with with_cwd(cwd):
        return await _stream_reply(agent, conv, console, mode, cancel)
```

### `src/novacode/cli.py` 改造

```python
# 紧跟 subagent_catalog = subagent.load_catalog(root) 之后
try:
    worktree_mgr = worktree.Manager(root)
except Exception as exc:
    print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
    worktree_mgr = None
else:
    # 后台跑过期清理，不阻塞启动（_amain 是 async 函数，用 asyncio.create_task）
    asyncio.create_task(
        worktree_mgr.sweep_stale(datetime.now() - timedelta(hours=24))
    )

agent_tool = AgentTool(
    subagent_catalog,
    task_mgr,
    parent=None,
    bg_enabled=cfg.effective_enable_subagent_background(),
    worktree_mgr=worktree_mgr,
)

code = await run(
    # ... 既有参数 ...
    task_mgr=task_mgr,
    subagent_catalog=subagent_catalog,
    worktree_mgr=worktree_mgr,  # ch14
)
```

## 模块交互

**SubAgent + Worktree 启动链路:**

```
主 Agent 调 Agent 工具
  ↓
AgentTool.execute
  ↓
definition.isolation == "worktree"?
  ↓ yes（且强制前台，background=False）
_execute_with_worktree:
  1. name = random_agent_name()  # "agent-a" + token_hex(4)[:7]
  2. wt = await worktree_mgr.create(name, "HEAD", manual=False)
  3. notice = build_worktree_notice(parent_cwd, wt.path)
  4. task_text = notice + "\n\n" + prompt
  5. with with_cwd(wt.path):
  6.     final_text = await sub_agent.run_to_completion(sub_conv, task_text, events)
  7. report = await worktree_mgr.auto_cleanup(name)
  8. if report.kept: final_text += "\n[Worktree 保留: <path>, 分支 <branch>]"
  9. return final_text   # execute 包成 Result(content=final_text)
```

**工具调用的 cwd 解析链路:**

```
模型调 read_file(path="server.py")
  ↓
agent.execute → registry.execute("read_file", args)
  ↓
read_file_tool.execute(args)
  ↓
abs = tool.resolve_path("server.py")
  ↓
ContextVar cwd 非空 → abs = cwd + "/server.py"
ContextVar cwd 为空 → abs = 进程 cwd + "/server.py"
  ↓
Path(abs).read_text()
```

**TUI 主 Agent Run 入口（tui/run.py 主循环）:**

```
# 每次调 _stream_reply 前包 ctx cwd
active_cwd = active_cwd_box[0] or str(Path.cwd())
with with_cwd(active_cwd):
    used = await _stream_reply(agent, holder.conv, console, mode_box.mode, cancel)
# run_callback（/do、/review）同样包住
```

## 文件组织

```
src/novacode/worktree/                — 新子包
├── __init__.py                       — 导出 Manager / validate_slug / 错误类型
├── manager.py                        — Manager 类型 + 构造
├── create.py                         — create + 快速恢复 + post-creation setup
├── lifecycle.py                      — enter / exit / remove / auto_cleanup
├── sweep.py                          — sweep_stale
├── slug.py                           — validate_slug + flat_slug
├── session.py                        — WorktreeSession + JSON 持久化
└── git.py                            — _run_git / _has_worktree_changes / _resolve_head_sha_from_fs

src/novacode/tool/
├── ctx.py                            — 新增 with_cwd/cwd_from_ctx/resolve_path
├── bash.py                           — 改造:子进程 cwd=resolve_path("")
├── read_file.py                      — 改造:用 resolve_path
├── write_file.py                     — 改造:用 resolve_path
├── edit_file.py                      — 改造:用 resolve_path
├── glob_tool.py                      — 改造:用 resolve_path（注意文件名，非 glob.py）
└── grep_tool.py                      — 改造:用 resolve_path（注意文件名，非 grep.py）

src/novacode/subagent/
├── definition.py                     — 加 isolation 字段
└── parser.py                         — 解析 isolation:

src/novacode/agent/
├── agent_tool.py                     — execute 加 isolation 分支（强制前台）+ worktree_mgr 字段
└── agent_worktree.py                 — 新增:_execute_with_worktree + notice 构造

src/novacode/command/
├── builtin_worktree.py               — 新增:/worktree handler（经 CommandContext）
├── builtins.py                       — 注册 /worktree（args_handler）
├── command.py                        — Command 加 args_handler 字段
├── dispatch.py                       — parse 改三元组 + dispatch_slash 透传 tail
└── context.py                        — CommandContext 加 worktree_mgr / set_active_cwd

src/novacode/tui/
└── run.py                            — run() 加 worktree_mgr / active_cwd_box / cwd 注入

tests/
├── test_worktree_slug.py
├── test_worktree_manager.py
├── test_worktree_create.py
├── test_worktree_lifecycle.py
├── test_worktree_sweep.py
├── test_worktree_git.py
├── test_tool_ctx.py
├── test_subagent_parser.py           — 新增 isolation case
├── test_agent_worktree.py
├── test_command_dispatch.py          — 更新 test_parse 三元组断言
└── test_command_builtins.py          — 加 worktree 注册/分发测试

src/novacode/cli.py                   — 接入（Manager 降级 + sweep + AgentTool/run 传参）

.gitignore                            — 追加两行
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| cwd 传递方式 | `contextvars.ContextVar` + with_cwd 上下文管理器 | 项目首次引入 ContextVar 机制（无既有 ctx 范式）。Tool schema 不变，prompt cache 不抖动；asyncio task 自动继承 context |
| Worktree 目录位置 | `.novacode/worktrees/<flat_slug>/` | ch14 设计约定；仓库内 + .gitignore 不追踪 |
| 嵌套 slug `/` 处理 | 替换为 `+`(flat_slug)做文件系统/分支名 | Git 分支的 `/` 是命名空间分隔符，会导致 `worktree-team/alice` 与 `worktree-team` 的 D/F 冲突 |
| Manager 构造失败处理 | 抛异常，novacode 降级 worktree_mgr=None | 不阻塞 novacode 启动；后续 isolation:worktree 调用回错误信息 |
| 快速恢复 | 纯 fs read，不调 `git worktree add` | `git worktree add`/`rev-parse` 在大仓库下较慢；场景:同一 SubAgent 反复进同 worktree。fs 解析失败时 head_commit 置空，变更检查 fail-closed 兜底 |
| 创建后设置失败处理 | 仅 stderr 警告 | 都是 best-effort，失败 ≠ 不可用 |
| `-B` vs `-b` | `-B`(重置) | 上次残留的孤儿分支不会让 create 失败 |
| `await asyncio.sleep(0.1)` 在 remove | 保留 | git worktree remove 后 branch -D 存在 lockfile 竞态；100ms 是经验值 |
| os.chdir 使用场景 | 仅 Manager.exit 兜底一次 | 项目进程 cwd 从不改变（bash 用 create_subprocess_shell 继承），兜底为防御性；其他全部 explicit cwd |
| 后台清理触发时机 | novacode 启动时跑一次，asyncio.create_task 后台执行 | 不阻塞主流程；ch09 已有 session.clean_expired 同样做法（asyncio.to_thread） |
| `.worktreeinclude` 缺失行为 | 跳过 D 步骤，不报错 | 大多数项目没这文件 |
| `subagent.isolation` 默认值 | `""`(无隔离) | 不破坏 ch13 既有定义文件 |
| 临时 worktree 命名 | `agent-a<7hex>` | ch14 设计约定；sweep_stale 正则匹配 |
| Manager 用 asyncio.Lock 而非 threading.Lock | 整个项目跑在 asyncio 事件循环上，异步友好 | 子进程调用都是 await，避免线程锁阻塞事件循环 |
| command 包访问 worktree | CommandContext duck typing（`worktree_mgr: Any`） | command 包不 import worktree，避免反向依赖；handler 是唯一入口（ch10 废弃 UI Protocol 的延续） |
| TUI active_cwd | `tui/run.py` 内可变容器 `list[str]`，空 = 进程 cwd | 与 `_ModeBox` / `exit_flag` 同一模式；CommandContext 注入 set_active_cwd 回调 |
| session 恢复 | Manager.__init__ 读 session + `tui.run` 装配时设 active_cwd | 本项目无 `--resume` CLI 参数（恢复走 `/resume` 命令）；启动时统一处理 |
| Windows/WSL 跨平台 | symlink 用 `os.symlink(..., target_is_directory=True)` best-effort | Windows 上无权限时失败跳过（`os.symlink` 对目录需 `target_is_directory=True`，POSIX 忽略该参数）；端到端在 WSL tmux 跑 |
| git 子进程调用 | `asyncio.create_subprocess_exec` | 不阻塞事件循环；统一注入 env 与 stdin=DEVNULL |
| 子 Agent 临时名随机源 | `secrets.token_hex(4)[:7]` | 标准库，加密强随机，7 位 hex 与正则一致 |
