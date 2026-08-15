# Worktree 隔离 Spec

## 背景

ch13 SubAgent 已隔离消息、权限决策状态、文件读缓存和 token 计数，但**文件系统**仍然共享。主 Agent 和后台子 Agent（以及下一章要做的 Agent Team 队员）会在同一时刻并发读写同一份工作目录的文件，出现读到对方写了一半的文件、互相覆盖修改等并行冲突——本质就是经典的并行开发文件冲突，和两个程序员同时改同一份文件一样。

Git 分支只能做**时间维度**的隔离（切换分支时工作目录被覆盖，同一时刻只有一个工作目录），不能解决并行问题；切分支还会刷被切文件的 mtime，触发依赖追踪型构建工具的链式重编。

需要的是**空间维度**的隔离：同一仓库同时挂多个工作目录、共享版本库、各自一个分支。这就是 Git Worktree（Git 2.5+）的能力。本章在 novacode 中封装一层 Worktree 管理逻辑，把这块拼图补给 SubAgent，让后台 / 并行场景安全可用。

novacode 现有相关基础设施（ch13 起）：
- ch13 SubAgent 已支持 frontmatter（`src/novacode/subagent/parser.py`）。**注意 frontmatter 键是 camelCase**（`disallowedTools` / `permissionMode` / `maxTurns` / `background`），解析后 Python `Definition` 字段为 `tools / disallowed_tools / model / max_turns / permission_mode / dont_ask / background`。本章新增 `isolation`（frontmatter 键与字段同名，小写）。
- ch13 `agent.AgentTool.execute` 已是子 Agent 启动入口，本章在此处插桩 isolation 分支。
- ch08 文件读缓存（recovery）以绝对路径作为 key——Worktree 副本路径天然不同，进/出无需清缓存。
- ch10 `src/novacode/command` 已有 slash 命令注册系统；命令 handler 通过 **`CommandContext`**（`command/context.py`，dataclass）访问能力，这是唯一的抽象层。**不再引入 UI Protocol / Textual App**（ch10 修订时已废弃该设想）。
- `.gitignore` 已忽略 `.novacode/sessions/` 等子目录，本章追加 `.novacode/worktrees/` 与 `.novacode/worktree_session.json`。
- Tool 接口是 `execute(args: str) -> Result`（`args` 是 raw JSON 字符串，**无 ctx 参数、无 ToolContext**）。项目目前**没有任何 ContextVar 传递机制**——本章在 `tool/ctx.py` 新增 `contextvars.ContextVar` + `with_cwd` 上下文管理器，作为 explicit cwd 的传递通道（工具 schema 不变、prompt cache 不抖动）。
- 项目进程级 cwd 从不改变（bash 工具用 `asyncio.create_subprocess_shell` 继承进程 cwd，无 `os.chdir`）。本章的 ctx cwd 注入只影响「工具解析相对路径」与「bash 子进程 cwd 参数」，进程 cwd 保持稳定。

本章不引入 Worktree 间合并策略、跨目录代码同步、多 Agent 并行编排，这些属于上层 / 下一章范畴。

## 目标

- **G1**: 提供 `WorktreeManager` 封装 Worktree 完整生命周期——创建、快速恢复、进入、退出、删除；并发场景下用单一 `asyncio.Lock` 保护内部 `active` 映射
- **G2**: 名字 (slug) 严格安全校验——限字符集 `[a-zA-Z0-9._-]`、总长度上限 64、显式拒绝 `.` 和 `..` 段名、允许 `/` 做嵌套分隔；防 LLM 输入触发路径遍历
- **G3**: Worktree 目录统一落在仓库内不被追踪的位置 `.novacode/worktrees/<flat_slug>/`，分支名前缀 `worktree-<flat_slug>`，嵌套 slug 的 `/` 替换为 `+` 避免 Git D/F 冲突
- **G4**: 创建后做四类环境初始化——A 复制本地配置（`.novacode/config.yaml` / `.novacode/settings.local.yaml`）、B 配置子目录的 git hooks（`core.hooksPath` 不自动继承）、C 软链 `node_modules` / `.venv` / `vendor` 等大目录、D 按项目根 `.worktreeinclude` 复制被忽略但运行需要的文件；均为 best-effort，失败只警告不中断创建
- **G5**: 快速恢复——目录已存在时，仅读 `.git` 指针 + `HEAD` + `refs/` 文件系统读还原 commit SHA，不调 `git worktree add`，毫秒级返回（fs 解析失败时 head_commit 置空，由 fail-closed 变更检查兜底）
- **G6**: 进入 Worktree 不调 `os.chdir`——把 `WorktreePath` 记到会话状态（`WorktreeSession`）并通过 `tool/ctx.py` 的 ContextVar 传给工具调用；Bash / Read / Write / Edit / Glob / Grep 工具从 ctx 取 cwd，本次调用显式声明在 Worktree 里跑；进程级 cwd 不变，避免并发组件之间的同步点
- **G7**: 文件读缓存等以绝对路径为 key 的缓存，天然按目录隔离；进入 / 退出 Worktree **不需要清缓存**
- **G8**: 退出时变更保护——`action="remove"` 且未显式 `discard_changes=True` 时，检测到未提交修改或本地多于 base 的 commit 一律拒绝删除；同时 `os.chdir(original_cwd)` 兜底防 session 期间残留（防御性：项目进程 cwd 本身从不改变）
- **G9**: 自动清理 (`auto_cleanup`)——SubAgent 退出时，无变更则直接 remove，有变更则保留 Worktree 路径与分支名追加到 SubAgent 结果文本给主 Agent review
- **G10**: 后台过期 Worktree 清理——按命名模式 (`agent-a[0-9a-f]{7}`) 只识别临时 Worktree，叠加时间过滤（超过 cutoff 才考虑），最后做 fail-closed 变更检查（有未提交修改 / 未推送 commit 都保留）
- **G11**: `WorktreeSession` 持久化到 `.novacode/worktree_session.json`，novacode 启动时读取并校验目录仍存在；退出时清空文件而不是删文件，确保下次启动不误恢复
- **G12**: 在 `subagent.Definition` 增加 `isolation` 字段 (`""` / `"worktree"`)；SubAgent 启动器检测到 `isolation:worktree` 后，自动 `create → inject worktree notice → set ctx cwd → run_to_completion → auto_cleanup`，无需在 prompt / 工具调用里显式指定
- **G13**: 提供 TUI slash 命令 `/worktree create <slug>`、`/worktree list`、`/worktree enter <slug>`、`/worktree exit [--remove]`、`/worktree remove <slug> [--discard]`——让用户手动管理；手动创建的 Worktree **不走自动清理**
- **G14**: 与 ch04~ch13 协同——主 Agent 看到的工具列表不变（ctx 注入不改 schema）、prompt cache 不抖动、既有测试不破坏

## 功能需求

### Slug 验证

- **F1**: `worktree.validate_slug(name)` 校验规则——
  - name 非空，总长度 ≤ 64
  - 按 `/` 切段，每段必须匹配正则 `^[a-zA-Z0-9._-]+$` 且不能是 `.` 或 `..`
  - 不允许出现连续 `//`、首末 `/`
  - 失败时抛 `ValueError` 携带具体原因

### WorktreeManager 与核心数据结构

- **F2**: `worktree.Worktree`(dataclass)记录单个 Worktree 的元信息——`name`(原始 slug)、`path`(绝对路径)、`branch`(`worktree-<flat_slug>`)、`based_on`(创建时的 base 引用，如 `HEAD` 或具体 commit)、`head_commit`(创建时的 commit SHA)、`created`(datetime)、`manual`(bool,是否用户手动创建，影响 auto_cleanup 跳过判断)
- **F3**: `worktree.WorktreeSession`(dataclass)记录当前活跃的 Worktree 会话——`original_cwd`、`worktree_path`、`worktree_name`(原 slug)、`original_branch`、`original_head_commit`、`session_id`(UUID 字符串)、`hook_based`(bool,预留)
- **F4**: `worktree.Manager` 内部字段——`repo_root`(绝对路径)、`worktree_dir`(`<repo_root>/.novacode/worktrees`)、`session_file`(`<repo_root>/.novacode/worktree_session.json`)、`lock: asyncio.Lock`、`active: dict[str, Worktree]`、`current_session: WorktreeSession | None`、`symlink_dirs: list[str]`(默认 `["node_modules", ".venv", "vendor"]`)
- **F5**: `worktree.Manager(repo_root: str)` 构造时(或工厂函数 `Manager.create(repo_root)`)——
  - 校验 `repo_root` 是 git 仓库根目录(`git rev-parse --show-toplevel` 输出与之等)；失败抛异常，novacode 启动允许降级到「Worktree 功能未启用」
  - 创建 `worktree_dir` 目录(如不存在)
  - 从 `session_file` 反序列化 `current_session`(允许文件不存在 / 内容为 `null` / 内容为非法 JSON——后两者只 stderr 警告并清空)；若 session 指向的 Worktree 目录已不存在，清空 session 文件并把 `current_session=None`
  - 扫描 `worktree_dir` 子目录还原 `active` 映射(name → Worktree)，仅按文件系统读填字段(快速恢复路径)
- **F6**: `Manager.create(name: str, base_ref: str, manual: bool) -> Worktree`(async)——
  - 1. `validate_slug(name)` 不通过即抛异常
  - 2. `async with self.lock:`，若 `active[name]` 已存在，抛异常
  - 3. `flat_slug = name.replace("/", "+")`、`wt_path = self.worktree_dir / flat_slug`、`branch_name = f"worktree-{flat_slug}"`
  - 4. 快速恢复路径:若 `wt_path` 已存在，用 `_resolve_head_sha_from_fs` 读 `.git` 指针 + `HEAD` + `refs/` 还原 `head_sha`，构造 `Worktree(...)` 加入 `active`，返回（不调 `git worktree add`）。fs 解析失败时 `head_commit` 置空串（变更检查对空 base 会 fail-closed 保留，安全）
  - 5. 否则执行 `git worktree add -B <branch> <wt_path> <base_ref>`，环境变量 `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=""`，stdin 关闭；失败时抛异常并 `shutil.rmtree(wt_path, ignore_errors=True)` 清理可能残留的目录
  - 6. 执行创建后设置 `_perform_post_creation_setup` (F7-F10)，任何子步骤失败仅 stderr 警告，不中断
  - 7. 读出 `head_sha`(`git -C <wt_path> rev-parse HEAD`)，装填 `Worktree(name, path, branch, based_on, head_commit, created, manual)`
  - 8. 加入 `active`，返回
- **F7**: 创建后设置 A——复制本地配置文件，从 `<repo_root>/.novacode/config.yaml` 与 `<repo_root>/.novacode/settings.local.yaml` 复制到 Worktree 同位置(目标目录已存在跳过，文件不存在跳过)
- **F8**: 创建后设置 B——配置 git hooks，优先检测主仓库 `.husky/` 目录，回退 `git -C <repo_root> config --get core.hooksPath`；若有值则 `git -C <wt_path> config core.hooksPath <绝对路径>`；无则跳过
- **F9**: 创建后设置 C——按配置软链大目录，默认列表 `["node_modules", ".venv", "vendor"]`，对每个目录若主仓库存在且 Worktree 不存在则创建 symlink (`os.symlink(..., target_is_directory=True)`，Windows 需该参数，POSIX 忽略)；其他失败只警告
- **F10**: 创建后设置 D——按项目根 `.worktreeinclude` 复制被忽略但运行需要的文件；读取 `.worktreeinclude` 每行为 glob 模式(支持 `*.env` 这种)，用 `git -C <repo_root> ls-files --others --ignored --exclude-standard --directory` 列出所有忽略文件，用 `fnmatch.fnmatch` 匹配模式后逐个复制到 Worktree 对应路径；文件不存在 / 模式无匹配只警告

### 进入与退出

- **F11**: `Manager.enter(name: str) -> WorktreeSession`(async)——
  - 1. `async with self.lock:`，从 `active` 取 wt(不存在抛异常)
  - 2. 取当前 `Path.cwd()` 与当前 `repo_root` 的 Git HEAD/branch 作为原状态（`rev-parse --abbrev-ref HEAD` / `rev-parse HEAD`，失败用空串兜底）
  - 3. 构造 `WorktreeSession(original_cwd, worktree_path=wt.path, worktree_name=name, original_branch, original_head_commit, session_id=uuid)`
  - 4. 写 `self.current_session = session`，持久化到 `session_file`(原子写——先写 tmp 再 rename)
  - 5. 返回 session
  - **不调 `os.chdir`**
- **F12**: `Manager.exit(name: str, action: ExitAction, opts: ExitOptions) -> ExitReport`(async)——`ExitAction` 取 `KEEP` / `REMOVE` 枚举；`ExitOptions(discard_changes: bool)`
  - 1. `async with self.lock:`，取 `active[name]` 与 `current_session`(若 `current_session.worktree_name != name` 抛异常，只能退当前)
  - 2. 若 `action=REMOVE` 且 `not opts.discard_changes`，调 `_has_worktree_changes(wt.path, wt.head_commit)`，有变更则抛 `WorktreeHasChangesError`
  - 3. `os.chdir(session.original_cwd)` 兜底（防 session 期间 Bash 残留；进程 cwd 默认从未改变，此处为防御性）
  - 4. `self.current_session = None`，持久化为空 JSON `null` 字符串
  - 5. 若 `action=REMOVE`：`git worktree remove --force <wt_path>` → `await asyncio.sleep(0.1)` → `git branch -D <branch_name>`；`del active[name]`
  - 6. 返回 `ExitReport(removed: bool, path: str, branch: str)`
- **F13**: `Manager.remove(name: str, opts: ExitOptions)`——独立 remove 入口，允许删除非当前 session 的 Worktree；变更保护同 F12
- **F14**: `Manager.auto_cleanup(name: str) -> AutoCleanupReport`——
  - 1. 取 `active[name]`，`manual=True` 直接 `keep`
  - 2. `_has_worktree_changes(wt.path, wt.head_commit)` 返回 False 走 `remove(name, ExitOptions(discard_changes=True))`，报告 `AutoCleanupReport(kept=False)`
  - 3. 有变更：`AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)`
- **F15**: `_has_worktree_changes(wt_path, base_commit) -> bool`——两件事：`git -C <wt_path> status --porcelain` 非空即有未提交；`git -C <wt_path> rev-list --count <base_commit>..HEAD` >0 即有新增 commit；任一 git 命令本身出错 fail-closed 返回 True(宁可保留)

### explicit cwd 工具改造

- **F16**: 在 `novacode.tool.ctx` 定义 ctx key 与帮助函数——
  - Python 用 `contextvars.ContextVar("cwd", default=None)` 实现 ctx 传递（项目首次引入 ContextVar 机制，与 Tool schema 解耦）
  - `with_cwd(dir: str)` 返回 context manager 设置 ContextVar token
  - `cwd_from_ctx() -> str | None` 取回
  - `resolve_path(p: str) -> str`——若 p 是绝对路径直接返回；否则用 ctx cwd(优先)或进程 cwd 拼相对路径，返回绝对路径
- **F17**: 改造 6 个核心工具支持 ctx cwd——
  - `read_file`、`write_file`、`edit_file`：用 `resolve_path` 解析 `path` 参数
  - `glob`（`tool/glob_tool.py`）、`grep`（`tool/grep_tool.py`）：用 `resolve_path` 解析 `path` 参数（schema 已有 `path`）
  - `bash`：在 `asyncio.create_subprocess_shell` 调用上设置 `cwd=resolve_path("")`（空串解析为 ctx cwd 或进程 cwd）
- **F18**: ctx cwd 注入点——
  - SubAgent isolation：worktree 启动时，在调 `run_to_completion` 前用 `with_cwd(wt.path)` 包住
  - TUI 主循环（`tui/run.py` 的 `run()`）维护 `active_cwd`（可变容器，空=进程 cwd）；`/worktree enter` 命令更新它，主 Agent 每次 Run（`_stream_reply`）前用 `with_cwd(active_cwd)` 包住
- **F19**: 工具 Schema 不变——主 Agent 看到的工具列表与参数与 ch13 完全一致，ctx 注入不暴露 cwd 字段

### SubAgent 集成

- **F20**: 扩展 `subagent.Definition` 增加 `isolation: str` 字段；`parser.py` 解析 frontmatter `isolation:` 字段（小写键，与其它 camelCase 键不同），合法值 `""` / `"worktree"`，非法值 stderr 警告后回落到 `""`
- **F21**: 改造 `agent.AgentTool.execute`——当 `definition.isolation == "worktree"` 时走 `_execute_with_worktree` 分支：
  - 1. 用 `worktree.random_agent_name()`（`"agent-a" + secrets.token_hex(4)[:7]`）作为 worktree name（规避同类型并发冲突）
  - 2. 调 `worktree_manager.create(name, "HEAD", manual=False)` 创建临时 Worktree
  - 3. 构造 `worktree_notice` 文本(F22)拼到 task 文本前
  - 4. 用 `with_cwd(wt.path)` 包住后续调用
  - 5. 调 `sub_agent.run_to_completion(sub_conv, task_with_notice, events)`
  - 6. 跑完后调 `manager.auto_cleanup(name)`，`kept=True` 时把 `\n[Worktree 保留: <path>, 分支 <branch>]` 追加到 final_text
  - 7. 返回 final_text 给主 Agent（`execute` 包成 `tool.Result(content=final_text)`）
- **F22**: `build_worktree_notice(parent_cwd: str, wt_path: str) -> str` 模板（实际内容大致如下，中文友好）——
  ```
  <worktree-context>
  你当前在一个独立的 Git Worktree 副本中工作，与父 Agent 隔离。
  - 父目录: <parent_cwd>
  - 你的工作目录: <wt_path>
  - 父 Agent 提到的绝对路径基于父目录，你需要翻译成本地路径（替换前缀）再读写
  - 编辑文件前，必须先在本地 Worktree 重新 `read_file` 一次，避免使用过时内容
  </worktree-context>
  ```
- **F23**: 后台 SubAgent + isolation 协同——**本期最小实现：`isolation: worktree` 时强制走前台同步**（忽略 `background` 字段与 `run_in_background`），Worktree 创建与 auto_cleanup 都发生在前台 `run_to_completion` 路径内；主 Agent 拿到的是最终文本而非 `task_id`

### TUI Slash 命令

- **F24**: `/worktree create <slug>`——调 `manager.create(slug, "HEAD", manual=True)`，输出 Worktree path + branch
- **F25**: `/worktree list`——遍历 `manager.list()`，每行格式 `<name>  <path>  <branch>  [manual]`（当前 session 的 worktree 附加 `[active]`）
- **F26**: `/worktree exit [--remove] [--discard]`——退出当前 session；`--remove` 时调 `exit(name, ExitAction.REMOVE, ExitOptions(discard_changes=discard))`，`--discard` 跳过变更保护
- **F27**: `/worktree remove <slug> [--discard]`——直接调 `manager.remove(slug, ...)`
- **F28**: `/worktree enter <slug>`——调 `manager.enter(slug)`，更新主循环的 `active_cwd` 为 `session.worktree_path`，主 Agent 下次 Run 用这个 cwd 注入 ctx
- **F29**: 命令带参数机制与命令类别——
  - ch10 F7 是「零参数」设计（`parse` 对尾随参数直接吞掉让 lookup miss）。**本章把 `parse` 改为返回三元组 `(name, tail, is_slash)`**，`Command` 增加可选字段 `args_handler: Callable[[CommandContext, str], Awaitable[None]] | None`，`dispatch_slash` 命中命令后若 `args_handler` 非空则传入 `tail` 执行，否则走原 `handler(ctx)`（既有 12 条命令不受影响）
  - 这是对 ch10 F7 的有意修订：`/help xx` 从「未知命令」变为「显示 help」，需同步更新 `tests/test_command_dispatch.py` 的 `test_parse` 断言与 ch10 checklist F7 验收
  - `/worktree` 注册为 `Kind.UI`（改 TUI `active_cwd` 状态、创建/删除目录），不进对话历史
  - handler 经 `CommandContext` 访问能力：新增 `worktree_mgr`（`worktree.Manager | None`，duck typing）与 `set_active_cwd`（`Callable[[str], None]`，更新主循环 active_cwd）两个字段；输出走 `ctx.console.print`。**不引入 UI Protocol / `command/ui.py` / `tui/worktree_adapter.py`**（ch10 已废弃该设想，command 包不 import worktree 包避免反向依赖）

### 持久化与恢复

- **F30**: `WorktreeSession` 序列化为 JSON，字段名采用小写下划线；原子写——先写 `<session_file>.tmp` 再 `os.replace`；session 为 None 时写 `null`
- **F31**: novacode 启动时（`Manager.__init__` 内），读 `session_file` 反序列化；若文件内容为 `null` 或空，`current_session=None`；若 `worktree_path` 不存在或文件是非法 JSON，清空文件并 `current_session=None`（stderr 警告）
- **F32**: 启动装配阶段（`tui.run` 内）若 `manager.current_session()` 非空，把 `active_cwd` 设为 `session.worktree_path`，主 Agent 后续工具调用都按 explicit cwd 走。注：本项目没有 `--resume` CLI 参数（恢复走 `/resume` slash 命令），session 恢复统一由启动装配处理

### 后台过期清理

- **F33**: `Manager.sweep_stale(cutoff: datetime) -> list[str]`(async)——
  - 1. 遍历 `worktree_dir.iterdir()`
  - 2. **第一层** 名字匹配正则 `^agent-a[0-9a-f]{7}$`（本期只识别 SubAgent 临时模式）
  - 3. **第二层** 目录 mtime > cutoff 跳过；`current_session.worktree_path == 子目录` 跳过
  - 4. **第三层** 变更检查（fail-closed）：`git -C <子目录> status --porcelain` 非空跳过；`git -C <子目录> rev-list --max-count=1 HEAD --not --remotes` 非空跳过（有未推送 commit 也保留）
  - 5. 通过三层的子目录调 `remove(name, ExitOptions(discard_changes=True))`，记入 `removed`
- **F34**: novacode 启动时跑一次 `asyncio.create_task(manager.sweep_stale(now - timedelta(hours=24)))`（异步、后台执行），不阻塞启动

### .gitignore 更新

- **F35**: 在项目根 `.gitignore` 追加 `.novacode/worktrees/` 与 `.novacode/worktree_session.json` 两行（开发期手动操作）。novacode **不自动读取或修改用户 `.gitignore`**（尊重用户配置）

## 非功能需求

- **N1**: 主 Agent 看到的工具列表稳定——ctx 注入不改 schema，既有缓存不抖动
- **N2**: Worktree 创建后设置失败 (F7-F10) 不阻塞创建；主路径只在 git worktree add 本身失败时抛异常
- **N3**: Manager 所有状态变更受 `asyncio.Lock` 保护；Worktree 内部 git 操作不持锁，避免长锁
- **N4**: `os.chdir` 在 novacode 进程内只出现在 `Manager.exit` 兜底调用（防御性——本项目进程 cwd 从不改变）；其他地方一律用 explicit cwd
- **N5**: Worktree session 文件被破坏(非法 JSON)启动时只警告并清空，不阻断 novacode 启动
- **N6**: 与 ch04~ch13 既有测试零破坏——`pytest` 全绿（`test_parse` 的 ch10 断言按 F29 修订同步更新）
- **N7**: 中文友好——错误消息与命令输出全部中文(对齐 novacode 其他模块风格)

## 不做的事

- Worktree 间的合并策略(交给上层 `git merge` / `git cherry-pick`)
- 跨 Worktree 代码同步、文件 watcher
- 多 Agent 并行编排 / Agent Team(下一章)
- 主 Agent 用专用 merge 工具
- Plugin 来源的 Worktree 配置
- Windows 平台特殊支持（symlink 行为在 Windows 上不保证，best-effort 跳过）。开发与端到端以 **Windows 本机 + WSL tmux** 为主（见 ch13 环境约定）
- 跨 novacode 进程实例的 Worktree 共享(同一仓库同一时刻只支持一个 novacode 实例操作 worktree session)
- Worktree 内部 git 操作的 retry / exponential backoff(用一次性 `await asyncio.sleep(0.1)` 解决 lockfile 竞态即可)

## 验收标准

- **AC1**: `worktree.validate_slug` 对 `"feature/a"` 通过，对 `"../etc"` / `".."` / `"a//b"` / `"a/b "` 拒绝
- **AC2**: `manager.create("alice", "HEAD", manual=True)` 在 `.novacode/worktrees/alice/` 下落地 Worktree，分支为 `worktree-alice`
- **AC3**: `manager.create("team/alice", "HEAD", manual=True)` 在 `.novacode/worktrees/team+alice/` 下落地，分支 `worktree-team+alice`
- **AC4**: 已存在 worktree 目录时再调 create 走快速恢复——**不调 `git worktree add`**（纯 fs 读），毫秒级返回；单测可 monkeypatch `_run_git` 断言 `worktree add` 未触发
- **AC5**: 创建后设置 A——主仓库存在 `.novacode/settings.local.yaml` 时，Worktree 内同位置出现该文件
- **AC6**: 创建后设置 B——主仓库 `.husky/` 存在时，Worktree 的 `.git/config` 含 `core.hooksPath`
- **AC7**: 创建后设置 C——主仓库有 `node_modules/` 时，Worktree 内是软链(`Path.is_symlink()` 为 True)
- **AC8**: 创建后设置 D——主仓库有 `.worktreeinclude` 含 `*.env`，且主仓库存在被忽略的 `.env`，Worktree 内出现 `.env`
- **AC9**: `manager.enter(name)` **不**改变进程 `Path.cwd()`；返回 session 含正确字段
- **AC10**: `manager.exit(name, ExitAction.REMOVE, ExitOptions())` 当 Worktree 有未提交修改时，抛 `WorktreeHasChangesError`，Worktree 目录仍在
- **AC11**: `manager.exit(name, ExitAction.REMOVE, ExitOptions(discard_changes=True))` 显式 discard 时，目录被删，分支被删
- **AC12**: `manager.auto_cleanup(name)` 对 `manual=True` 直接 keep；对 `manual=False` 且无变更直接 remove
- **AC13**: 工具 `read_file` / `write_file` / `edit_file` / `bash` / `glob` / `grep` 在 ctx 注入 cwd 后，以 cwd 为基准解析相对路径(单测断言)
- **AC14**: `bash` 工具在 ctx cwd 注入下，子进程 `cwd=` 参数为 ctx cwd(单测 / 集成测试可断言)
- **AC15**: `subagent.Definition.isolation == "worktree"` 时，`AgentTool.execute` 创建临时 Worktree、注入 worktree notice、传 ctx cwd、跑完后调 auto_cleanup
- **AC16**: SubAgent + worktree 路径上，子 Agent 写文件不影响主 Agent 工作目录(集成测试或 tmux 实跑可观察)
- **AC17**: `/worktree create alice` slash 命令成功落地 Worktree，`/worktree list` 输出含 alice
- **AC18**: `/worktree exit --remove` 在 Worktree 有未提交修改时报错；加 `--discard` 后成功删除
- **AC19**: `manager.sweep_stale(cutoff)` 只删命名匹配 `agent-a[0-9a-f]{7}` 的目录、跳过当前 session、跳过有变更或有未推送 commit 的目录
- **AC20**: `WorktreeSession` 持久化到 `.novacode/worktree_session.json`，启动时读取；指向的 Worktree 目录被外部删除后，启动时清空 session 并 stderr 警告
- **AC21**: 项目可启动 (`python -m novacode`)、所有单元测试通过 (`pytest`)、lint 通过 (`ruff check`)
- **AC22**: tmux 实跑（WSL）——`python -m novacode` 启动 + 触发 `isolation:worktree` 子 Agent 改文件 + 验证主目录 `server.py`(若改的是 `server.py`)未变，Worktree 副本里 `server.py` 已变；Worktree 留盘 / 自动清理符合预期
- **AC23**: `parse("/worktree create foo")` 返回 `("worktree", "create foo", True)`；`Command.args_handler` 命中时 dispatch 传入 `tail`（ch10 `test_parse` 断言按 F29 同步更新）
