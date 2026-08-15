# Agent Team Tasks

> 本章开发前已按项目实际修订（2026-08-13）。改动点：协作工具加 `Team` 前缀
> （避免与 ch13 后台元工具重名）、文件路径对齐（`cli.py`/`config.py` 单文件、
> `tui/run.py` 函数式主循环、无 Textual App）、接口名对齐（`send_message(name, message)`、
> `session.load_session`、`ExitOptions`）、新增 `tui/leadmail.py` 与 `team/filelock.py`。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/novacode/team/__init__.py` | 包导出 |
| 新建 | `src/novacode/team/types.py` | Team / TeammateInfo / BackendType / 异常类 |
| 新建 | `src/novacode/team/persistence.py` | sanitize、atomic_write_json、read_json、reload_from_disk_locked |
| 新建 | `src/novacode/team/manager.py` | Manager.create/get/delete + Team.add_member/set_member_active/remove_member/handle_task_done/poll_lead_mailboxes |
| 新建 | `tests/test_team_manager.py` | Manager 单测 |
| 新建 | `src/novacode/team/spawn.py` | spawn_teammate 主流程（实现 TeamHook） |
| 新建 | `tests/test_team_spawn.py` | spawn 单测（in-process 路径） |
| 新建 | `src/novacode/team/feature.py` | FORK_TEAMMATE flag 读取 |
| 新建 | `src/novacode/team/filelock.py` | acquire 文件锁（mailbox 与 tasks 共用） |
| 新建 | `tests/test_team_filelock.py` | 串行 / stale 锁测试 |
| 新建 | `src/novacode/team/mailbox/__init__.py` | Box.read/write/read_unread/mark_read |
| 新建 | `src/novacode/team/mailbox/message.py` | Message / MessageType |
| 新建 | `tests/test_team_mailbox.py` | 并发与 stale 锁测试 |
| 新建 | `src/novacode/team/registry/__init__.py` | AgentNameRegistry |
| 新建 | `tests/test_team_registry.py` | 注册/解析/反查测试 |
| 新建 | `src/novacode/team/tasks/__init__.py` | Task / Store |
| 新建 | `src/novacode/team/tasks/filter.py` | Filter / Patch + is_ready 计算 |
| 新建 | `tests/test_team_tasks.py` | CRUD + 依赖关系测试 |
| 新建 | `src/novacode/team/backend/__init__.py` | Backend Protocol + SpawnRequest + new_backend |
| 新建 | `src/novacode/team/backend/detect.py` | detect() |
| 新建 | `tests/test_team_backend_detect.py` | 检测逻辑测试 |
| 新建 | `src/novacode/team/backend/tmux.py` | Tmux Backend |
| 新建 | `tests/test_team_backend_tmux.py` | tmux 命令构造测试 |
| 新建 | `src/novacode/team/backend/iterm2.py` | Iterm2 Backend |
| 新建 | `tests/test_team_backend_iterm2.py` | iterm2 命令构造测试 |
| 新建 | `src/novacode/team/backend/inprocess.py` | InProcess Backend |
| 新建 | `tests/test_team_backend_inprocess.py` | in-process spawn 集成测试 |
| 新建 | `src/novacode/team/tools/team_create.py` | TeamCreate 工具 |
| 新建 | `src/novacode/team/tools/team_delete.py` | TeamDelete 工具 |
| 新建 | `src/novacode/team/tools/team_task_create.py` | TeamTaskCreate 工具 |
| 新建 | `src/novacode/team/tools/team_task_get.py` | TeamTaskGet 工具 |
| 新建 | `src/novacode/team/tools/team_task_list.py` | TeamTaskList 工具 |
| 新建 | `src/novacode/team/tools/team_task_update.py` | TeamTaskUpdate 工具 |
| 新建 | `src/novacode/team/tools/team_send_message.py` | TeamSendMessage 工具 |
| 新建 | `tests/test_team_tools.py` | 工具单测 |
| 新建 | `src/novacode/coordinator/__init__.py` | is_enabled/allowed_tools/system_prompt_suffix/env_truthy |
| 新建 | `tests/test_coordinator.py` | 双锁测试 |
| 新建 | `src/novacode/agent/team_hook.py` | TeamHook Protocol + TeamSpawnRequest + TeammateContext |
| 修改 | `src/novacode/agent/agent_tool.py` | 增加 team_name 参数 + team_hook 委托 |
| 新建 | `src/novacode/agent/team_mailbox.py` | ingest_team_mailbox（pre_iteration_reminders 闭包） |
| 修改 | `src/novacode/agent/__init__.py` | Agent 新增 exclude_tools / pre_iteration_reminders / set_permission_mode / set_allowed_tools |
| 修改 | `src/novacode/task/manager.py` | 加 name_reg 委托 + on_task_done 回调 |
| 修改 | `src/novacode/tool/filter.py` | 新增 TEAMMATE_TOOLS 并入 ALL_AGENT_DISALLOWED_TOOLS |
| 修改 | `src/novacode/command/builtins.py` | 注册 /team 命令（handle_team） |
| 新建 | `src/novacode/command/builtin_team.py` | /team list/info/delete/kill |
| 修改 | `src/novacode/command/context.py` | CommandContext 加 team_mgr 字段 |
| 修改 | `src/novacode/tui/run.py` | team_mgr/coordinator_mode 参数、主 Agent exclude_tools、_autonomous_turn、状态栏标签、consume_lead_mail 启动 |
| 新建 | `src/novacode/tui/leadmail.py` | consume_lead_mail / build_team_update_reminder / LeadMessage |
| 修改 | `src/novacode/config.py` | FeaturesConfig（coordinator_mode / fork_teammate） |
| 修改 | `src/novacode/permission/sandbox.py` | /tmp、/private/tmp 白名单 |
| 修改 | `src/novacode/cli.py` | argparse flags、wire team.Manager、注册 7 个新工具、--team-member 分支 |
| 新建 | `src/novacode/team/member.py` | run_team_member 自治循环（--team-member 子进程入口） |
| 修改 | `.novacode/config.yaml` | 加示例 features 段（可选，不强制） |

## T1: 基础类型 — `src/novacode/team/types.py`

**依赖:** 无
**步骤:**
1. 定义 `BackendType(StrEnum)` 含 `TMUX` / `ITERM2` / `IN_PROCESS`
2. 定义 `@dataclass class Team`（F1）：字段含 `_lock: asyncio.Lock`、`name`、`sanitized_name`、`lead_agent_id`、`backend`、`description`、`created_at`、`members`、派生路径字段（`config_dir`/`config_path`/`tasks_path`/`mailbox_dir`，序列化时跳过）
3. 定义 `@dataclass class TeammateInfo`（F2），字段对应 json key（下划线命名）
4. 定义异常类 `TeamNotFoundError` / `TeamHasActiveMembersError` / `MemberExistsError` / `MemberNotFoundError` / `InProcessTeammateNoSpawnError`，统一基类 `TeamError`

**验证:** `python -c "from novacode.team.types import Team, TeammateInfo, BackendType"` 不报错；`ruff check src/novacode/team/types.py` 通过

## T2: sanitize 与原子写 — `src/novacode/team/persistence.py`

**依赖:** T1
**步骤:**
1. 实现 `sanitize(name: str) -> str`——只保留 `[a-zA-Z0-9._-]`，其他字符替换为 `-`，首尾去 `-`，空字符串返回 `""`（用 `re.sub`）
2. 实现 `atomic_write_json(path: str | Path, value: Any) -> None`——`json.dumps(indent=2)` → 写 `<path>.tmp` → `os.replace`
3. 实现 `read_json(path: str | Path) -> Any`——`Path.read_text()` + `json.loads`，文件不存在抛 `FileNotFoundError`
4. 实现 `async def reload_from_disk_locked(team: Team) -> None`——调用方持锁；从 `team.config_path` `read_json`，把 `members` 字段覆盖到 in-memory（失败静默回退到内存现状）

**验证:** 单测断言 `sanitize("foo bar/baz")=="foo-bar-baz"`；`atomic_write_json` 写入后 `read_json` 取回相等

## T3: Manager 与持久化 — `src/novacode/team/manager.py`

**依赖:** T1, T2
**步骤:**
1. 定义 `class Manager`（F3）
2. 实现 `Manager.__init__(home_dir: Path, project_root: str, wt_mgr, task_mgr, reg)`（F4）
   - 创建 `<home_dir>/.novacode/teams/` 目录
   - 扫描子目录，逐个读 `config.json`（失败 stderr 警告并跳过），反序列化后填充派生路径字段
3. 实现 `Manager.get(name: str) -> Team | None`（sanitize 后查询）
4. 实现 `Manager.list_() -> list[Team]`（按创建时间排序）
5. 实现 `async Manager.create(name, description="", agent_type="")`（F5）
   - sanitize + 同名冲突 `-2`/`-3` 后缀
   - 调 `detect_backend()`（T11）写 `team.backend`
   - 创建 `config_dir`、`mailbox_dir`
   - 注册 Lead 成员（`TeammateInfo(name="lead", agent_id="lead", is_active=None)`）
   - `atomic_write_json`
6. 实现 `async Manager.delete(name, force)`（F7 + F66）:
   - 持锁、找到 Team、（force=False 时）校验全员 is_active=False
   - 对每个非 lead 成员：用 `new_backend(mem.backend_type)` 解析后端，调 `await backend.kill(mem.pane_id, mem.agent_id)` 杀 pane（tmux/iterm2）或 cancel asyncio task（in-process）；失败只警告
   - 调 `_cleanup_member_resources` 删 session 目录与 worktree（`wt_mgr.remove(name, ExitOptions(discard_changes=True))`，best-effort）
   - `shutil.rmtree(team.config_dir)` 删整个 Team 目录
   - 从 in-memory dict 移除

**验证:** 写单测覆盖 create/get/delete 基本流程；`pytest tests/test_team_manager.py` 通过；tmux 实跑后 `/team delete --force` 看 pane 真的被杀（`tmux list-panes` 只剩 Lead）

## T3b: 跨进程并发兜底（reload-before-modify）

**依赖:** T3
**步骤:**
1. `Team.add_member` 与 `Team.set_member_active`（以及任何会修改 members 后 save 的方法）在加锁后**先** `await reload_from_disk_locked(self)` 再操作内存 + save
2. 意义：Pane 后端的 Lead 与子进程是两个独立进程，各持一份内存中的 Team。不 reload 会出现"子进程读 config 时 Lead 的 add_member 还没写入，子进程修改自己内存 Team 没看见自己，set_member_active 静默 no-op"的丢更新

**验证:** 单测构造时序：t1 = read_json 得到无 alice 的 Team A；t2 在 disk 上写带 alice 的 Team B；t3 调 `await team.set_member_active("alice", False)` 应该成功（走 reload 路径）而非静默 no-op

## T4: Team 成员操作 — 继续 `src/novacode/team/manager.py`

**依赖:** T3
**步骤:**
1. 实现 `async Team.add_member(info)`（F8）——加锁后**先 reload_from_disk_locked**，检查重名；加入 members；持久化
2. 实现 `async Team.set_member_active(name, active)`（F9）——加锁后**先 reload_from_disk_locked**；遍历 members 找到 name 改 is_active 字段；持久化
3. 实现 `async Team.remove_member(name)`（F10）
4. 实现 `Team.member_by_name(name) -> TeammateInfo | None` / `Team.member_by_agent_id(id_) -> TeammateInfo | None` 工具方法

**验证:** 单测覆盖 add → set_active → remove 三步流程，读回 config.json 校验字段

## T5: 共用 filelock — `src/novacode/team/filelock.py`

**依赖:** 无
**步骤:**
1. 实现 `async def acquire(lock_path: str) -> AsyncContextManager[None]`——
   - 循环 10 次：`os.open(lock_path, O_CREAT|O_EXCL|O_WRONLY, 0o644)` 抢锁
   - 失败时 `Path(lock_path).stat()`，若 `time.time() - st_mtime > 10` 则 `os.unlink(lock_path)` 后立即重试一次
   - 失败时 `await asyncio.sleep(random.uniform(0.005, 0.1))` 抖动后继续
   - context manager 退出时 `os.unlink(lock_path)`
2. 内部常量 `LOCK_MAX_RETRIES = 10` / `LOCK_STALE_AFTER = 10.0` / `LOCK_BACKOFF_MIN = 0.005` / `LOCK_BACKOFF_MAX = 0.1`

**验证:** 单测 `test_acquire_lock_serial`（两次抢锁，中间 release）、`test_acquire_lock_stale`（故意创建 11 秒前的锁——`os.utime(lock_path, (now-11, now-11))`，断言能拿到）

## T6: mailbox Message 与 Box — `src/novacode/team/mailbox/`

**依赖:** T5
**步骤:**
1. `message.py` 定义 `MessageType(StrEnum)` 与 4 个常量（F32）
2. `message.py` 定义 `@dataclass class Message`（F32），提供 `to_dict()` / `from_dict()`（注意 `from_` 字段对应 json key `"from"`）
3. `__init__.py` 定义 `class Box`，字段 `_dir: str`
4. 实现 `Box.__init__(dir_)`——`Path(dir_).mkdir(parents=True, exist_ok=True)`
5. 实现 `async Box.write(agent_id, msg)`（F33）
   - `lock_path = f"{self._dir}/{agent_id}.lock"`
   - `async with acquire(lock_path):`
   - 读 `<dir>/<agent_id>.json`（不存在视为 `{"messages":[]}`）
   - 追加 msg（若 timestamp=0 设为 `int(time.time())`）
   - `atomic_write_json`
6. 实现 `async Box.read(agent_id) -> list[Message]`
7. 实现 `async Box.read_unread(agent_id) -> tuple[list[int], list[Message]]`——返回 unread 消息的 indices 与消息本身
8. 实现 `async Box.mark_read(agent_id, indices)`——按 indices 把对应消息 `read=True`

**验证:** 单测覆盖 write/read/read_unread/mark_read；并发测试 10 个 asyncio task 写同一 agent_id，断言读回 10 条无丢失

## T7: AgentNameRegistry — `src/novacode/team/registry/__init__.py`

**依赖:** 无
**步骤:**
1. 定义 `class AgentNameRegistry`
2. 实现 `register(name, agent_id)`——若 name 已存在覆盖（取出旧 agent_id，从 `_by_id` 删旧映射）；若 agent_id 已有其他 name，先反向 unregister
3. 实现 `unregister(name)` / `unregister_by_agent_id(agent_id)`
4. 实现 `resolve(name_or_id) -> str | None`——先按 name 查，再按 agent_id 反向查
5. 实现 `name_of(agent_id) -> str | None`
6. 实现 `list_() -> dict[str, str]`

**验证:** 单测覆盖 register/unregister/resolve/name_of；包括「同名覆盖」和「不同名指向同一 agent_id」边界

## T8: tasks Store — `src/novacode/team/tasks/`

**依赖:** T5（用 filelock 的 acquire）
**步骤:**
1. `tasks/__init__.py` 定义 `Status(StrEnum)` / `Task` / `Filter` / `Patch` 类型（F30）
2. 定义 `class Store`，字段 `_path: str`、`_lock: asyncio.Lock`
3. 实现 `__init__(path)`
4. 实现 `async create(t) -> str`——生成 `task_<6 位 hex>` ID（`secrets.token_hex(3)`）；read-modify-write `tasks.json`（用 `team.filelock.acquire`，路径 `<path>.lock`）
5. 实现 `async get(id_) -> Task`
6. 实现 `async list_(f: Filter) -> list[Task]`——按 `status` 过滤，返回时附加 `is_ready` 字段（检查 blocked_by 中所有任务是否 completed）；`is_ready` 在输出时计算，不存盘
7. 实现 `async update(id_, p: Patch)`——支持 title/description/status/assignee/add_blocks/add_blocked_by/remove_blocks/remove_blocked_by 字段
8. `add_blocked_by=[X]` 同时给 X 任务 `blocks` 加上当前任务 id（双向维护）

**验证:** 单测覆盖 create/get/update；特别测 add_blocked_by 的双向更新

## T9: backend Protocol — `src/novacode/team/backend/__init__.py`

**依赖:** T1
**步骤:**
1. 定义 `@dataclass class SpawnRequest`（F13）——其中 `sub_agent` / `conv` / `task_mgr` 字段类型为 `Any`，避免 backend 反向依赖 agent 包的类型
2. 定义 `Backend` Protocol（F12）：`type() -> BackendType`、`async spawn(req) -> tuple[str, str]`（返回 `(pane_id, agent_id)`）、`async wake(pane_id, agent_id) -> None`、`async kill(pane_id, agent_id) -> None`
3. 定义 `def new_backend(t: BackendType, **deps) -> Backend` 工厂——按类型分发（具体实现在 T10-T12）

**验证:** `python -c "from novacode.team.backend import Backend, SpawnRequest, new_backend"` 通过

## T10: detect_backend — `src/novacode/team/backend/detect.py`

**依赖:** T9
**步骤:**
1. 实现 `def detect() -> BackendType`（F14）:
   - `os.environ.get("TMUX")` → `TMUX`
   - `os.environ.get("TERM_PROGRAM") == "iTerm.app"` 且 `shutil.which("it2")` → `ITERM2`
   - `shutil.which("tmux")` → `TMUX`
   - 否则 `IN_PROCESS`

**验证:** 写 test 用 `monkeypatch.setenv` 控制环境变量 + monkeypatch.setattr 替换 `shutil.which`，断言不同组合的返回值

## T11: tmux backend — `src/novacode/team/backend/tmux.py`

**依赖:** T9
**步骤:**
1. 定义 `class TmuxBackend`，`__init__()` 与 `type()` 返回 `BackendType.TMUX`
2. 实现 `async spawn(req)`（F15）:
   - 在 `$TMUX` 内：`tmux split-window -h -P -F "#{pane_id}" -- <cmd>`
   - 在 `$TMUX` 外但 `tmux` 二进制可用：`tmux new-session -d`（detached 新会话）走外部 session（F16）
   - `cmd` 构造 `build_member_cmd(req)`:`python -m novacode --team-member --team <team_name> --member <member_name> --agent-id <agent_id> --session-dir <session_dir> --worktree <wt_path> [--agent-type <type>] [--model <model>] [--plan-mode]`（可用 `shlex.quote` 转义）
   - `--agent-id` 必须传——子进程不需要读 Lead 还没写完的 `config.json` 找自己
   - `initial_prompt` **不**走命令行，由 `team.spawn_teammate`（T18）在 `backend.spawn` 之前预写入 alice mailbox
   - 用 `await asyncio.create_subprocess_exec("tmux", ...)` 跑 tmux，捕获 stdout 作为 pane_id
3. 实现 `async wake(pane_id, agent_id)`:`tmux send-keys -t <pane_id> "" Enter`
4. 实现 `async kill(pane_id, agent_id)`:`tmux kill-pane -t <pane_id>`，忽略 pane not found 错误

**验证:** 单测断言 `build_member_cmd` 命令字符串构造正确（用 monkeypatch.setattr 替换 `asyncio.create_subprocess_exec` 收 args）；集成测试在无 tmux 环境跳过

## T12: iterm2 backend — `src/novacode/team/backend/iterm2.py`

**依赖:** T9
**步骤:**
1. 实现 `Iterm2Backend.spawn`:`it2 split --new-pane --command "<cmd>"`，`<cmd>` 同 T11 格式（含 `--agent-id`），`initial_prompt` 走 mailbox 预写
2. 实现 `wake`:`it2 send-text --pane <pane_id> ""`
3. 实现 `kill`:`it2 close-pane --pane <pane_id>`

**注意:** iterm2 后端无法在 CI/本项目实跑，实现以构造正确的命令字符串为准

**验证:** 单测断言命令构造正确

## T13: in-process backend — `src/novacode/team/backend/inprocess.py`

**依赖:** T9，需要 `task` 包（`sub_agent`/`conv` 由调用方预构造，避免强依赖 `agent` 类型）
**步骤:**
1. 定义 `class InProcessBackend`，字段 `_task_mgr: task.Manager`
2. 实现 `async spawn(req)`（F18）:
   - 从 `req.sub_agent` / `req.conv` 取已构造好的对象
   - 调 `await task_mgr.launch(req.sub_agent, req.conv, req.member_name, req.initial_prompt)` 起 asyncio task
   - 返回 `("", task_id)`——in-process 用 agent_id 作为目标 id，pane_id 为空
3. 实现 `async wake(pane_id, agent_id)`:no-op，直接 return
4. 实现 `async kill(pane_id, agent_id)`:`await task_mgr.stop(agent_id)`

**验证:** 单测：构造 fake task_mgr，spawn 一个 noop 子 Agent，断言 asyncio task 启动

## T14: feature flag — `src/novacode/team/feature.py`

**依赖:** 无
**步骤:**
1. 实现 `def fork_teammate_enabled(cfg: Config) -> bool`——读 `cfg.features.fork_teammate`

**验证:** 单测覆盖 True/False 两种 cfg

## T15: team_hook 与 TeammateContext — `src/novacode/agent/team_hook.py`

**依赖:** 无
**步骤:**
1. 定义 `TeamHook` Protocol（`spawn_teammate` / `is_teammate_context`，plan.md 已给签名）
2. 定义 `@dataclass class TeamSpawnRequest`（把 Agent 工具参数传过去：team_name/member_name/agent_type/model/plan_mode_required/prompt）
3. 定义 `@dataclass class TeammateContext`——`team_name`、`member_name`、`agent_id`、`backend_type`、`mailbox_dir`、`read_unread`/`mark_read` 闭包（`Callable`）、`send_message_wake: Callable[[str], Awaitable[None]] | None` 等
4. 提供 `WITH_TEAMMATE_KEY = "teammate"` + `with_teammate_context(ctx, tc) -> dict` + `teammate_context_from_ctx(ctx) -> TeammateContext | None`（用 `contextvars.ContextVar`，参照 ch14 `tool/ctx.py::with_cwd` 先例；队员 run 期间 `with with_teammate_context(...)` 包住）
5. 定义 `MailboxReader` Protocol（`read_unread` / `mark_read`）——agent 包不 import `mailbox`

**验证:** `python -c "from novacode.agent.team_hook import TeamHook, TeammateContext"` 通过

## T16: Agent 构造扩展 — `src/novacode/agent/__init__.py`

**依赖:** T15
**步骤:**
1. `Agent.__init__` 新增可选参数 `exclude_tools: list[str] = []`——`run` 的 defs 三分支（`definitions_filtered` / `read_only_definitions` / `definitions`）结果过滤掉 exclude 名（主 Agent 排除协作工具用，不破坏 plan 只读收窄）
2. `Agent.__init__` 新增可选参数 `pre_iteration_reminders: Callable[[], Awaitable[list[str]]] | None = None`——`run` 循环每轮 `self.runtime.take_reminders()` 之后调用，返回值并入 reminder（队员 mailbox 注入通道）
3. 新增方法 `set_permission_mode(mode: Mode)`——写 `self._permission_mode`
4. 新增方法 `set_allowed_tools(allowed: list[str] | None)`——写 `self._allowed_tools`

**验证:** `python -c "from novacode.agent import Agent"` 通过；单测：`exclude_tools` 过滤 defs、`pre_iteration_reminders` 返回并入 reminder、`set_permission_mode` 后 `run_to_completion` 用新模式

## T17: 工具过滤 — `src/novacode/tool/filter.py`

**依赖:** 无
**步骤:**
1. 新增常量:
   ```python
   TEAMMATE_TOOLS: list[str] = [
       "TeamTaskCreate", "TeamTaskGet", "TeamTaskList", "TeamTaskUpdate", "TeamSendMessage",
   ]
   ```
2. 把 `TEAMMATE_TOOLS` 并入 `ALL_AGENT_DISALLOWED_TOOLS`——普通子 Agent 默认看不到协作工具
3. **不**扩展 `FilterParams` 加 teammate 字段——队员工具集由 Team 模块装配（`apply_agent_tool_filter(基础) + TEAMMATE_TOOLS`），符合「注册进去，不是过滤留下的」（HANDOFF ch13 设计决策 2）

**验证:** 单测：`apply_agent_tool_filter(基础)` 不含 `TeamTaskCreate` 等；`filter(基础) + TEAMMATE_TOOLS` 含全部 5 个

## T18: spawn_teammate 主流程 — `src/novacode/team/spawn.py`

**依赖:** T1-T17
**步骤:**
1. 定义 `async Manager.spawn_teammate(req: TeamSpawnRequest) -> str`（实现 TeamHook）
2. 实现 plan.md 中描述的步骤流程:
   - 取 Team（`manager.get`）
   - 校验调用者权限（看 ctx 是否有 TeammateContext；in-process 队员 → 抛 `InProcessTeammateNoSpawnError`）
   - 解析 `SubAgentDefinition`（指定 subagent_type 走 Catalog；留空且 `fork_teammate_enabled` 走 fork_definition；留空且 flag 关闭用 `general-purpose`）
   - `await wt_mgr.create(f"team-{sanitized}/{member}", "HEAD", False)` 创建 Worktree
   - 申请 session_dir（复用 ch12 格式，生成新 id）
   - 计算 allowed = `apply_agent_tool_filter(FilterParams(all=..., source=..., background=False))` + `TEAMMATE_TOOLS`；`plan_mode_required` 时改为只读基础工具 + `TEAMMATE_TOOLS`（不含写工具，F48）
   - 构造 sub_agent（**强制 `dont_ask=True`** F39a，`permission_mode=Mode.PLAN` 若 plan_mode_required）+ sub_conv，注入 `<team-context>` reminder（helper `build_team_context_reminder`）+ ctx 装 `TeammateContext`（read_unread/mark_read 闭包）
   - 若 backend_type=tmux/iterm2：`await Box(t.mailbox_dir).write(agent_id, Message(from_="lead", type=TEXT, summary=truncate_for_summary(prompt), content=req.prompt))` 预写初始任务（F13）
   - 预生成 agent_id（Pane 后端用）：`agent-` + `secrets.token_hex(7)`
   - `await backend.spawn(req)` 取 `(pane_id, agent_id)`
   - `registry.register(member_name, agent_id)`
   - `await team.add_member(...)`（reload_from_disk_locked 保护跨进程并发）
   - 返回 JSON `{"member_name", "agent_id", "worktree", "backend", "pane_id"}`
3. 提供 helper `team_system_prompt_suffix() -> str` 返回 F39 附录；`build_team_context_reminder(...)`；`truncate_for_summary(prompt)`

**验证:** 单测覆盖 in-process 后端的 spawn 全流程；Pane 后端的 spawn 用 mock backend

## T19: Agent 工具集成 — `src/novacode/agent/agent_tool.py`

**依赖:** T15, T18
**步骤:**
1. `AgentArgs` dataclass 加 `team_name: str = ""`
2. `AgentTool` 加字段 `team_hook: TeamHook | None = None`；`__init__` 加参数 `team_hook`
3. `description()` 中说明 `team_name` 参数；`parameters()` 加 `team_name` 字段
4. `execute` 在 `args.team_name != ""` 时:
   - 校验 `self.team_hook is not None`，否则抛错
   - 校验 ctx 不在 in-process 队员中（`self.team_hook.is_teammate_context(ctx)`，若是且 backend_type=in-process，抛 `InProcessTeammateNoSpawnError`）
   - 调 `await self.team_hook.spawn_teammate(TeamSpawnRequest(...))`，返回其结果
   - 注意：isolation=worktree 的队员 spawn 分支与 ch14 的 worktree 隔离不冲突（队员本身在 team worktree 内，`isolation` 字段仍走 ch14 逻辑）

**验证:** 单测：不带 team_name 走 ch13 老路径；带 team_name 调 mock team_hook，断言 spawn_teammate 被调

## T20: 队员 Loop incoming-messages 注入 — `src/novacode/agent/team_mailbox.py`

**依赖:** T15, T16, T6
**步骤:**
1. 实现 `ingest_team_mailbox(tc: TeammateContext) -> Callable[[], Awaitable[list[str]]]`——返回 pre_iteration_reminders 闭包:
   - `indices, unread = await tc.read_unread()`
   - 无未读返回 `[]`
   - 构造 `<incoming-messages>` reminder（F42 格式），`await tc.mark_read(indices)`，返回 `[reminder]`
2. 若有 `plan_approval_response(approve=True)` 消息:附加文案「Lead 已批准计划，权限模式已切到 default，可执行计划」；`approve=False` 附加「Lead 驳回了计划，反馈:<feedback>。请调整后重新提交」（T32）
3. Pane 后端的 plan_approval 由 `run_team_member` 主循环额外处理一份（读 mailbox 分流时）——它读到 plan_approval_response 时同样切模式 + 合成续派 prompt 让 `run_to_completion` 接着跑（F19a）

**验证:** 单测覆盖：fake mailbox 写入 1 条消息，闭包返回 reminder 含 `<incoming-messages>`；approve/reject 文案分支

## T21: task.Manager 改造 — `src/novacode/task/manager.py`

**依赖:** T7
**步骤:**
1. `Manager` 加字段 `name_reg: AgentNameRegistry | None = None`；加 `set_name_registry(reg)` setter
2. `launch` 时:若 `name_reg` 非 None 且 name 非空，调 `name_reg.register(name, id_)`；同时保持本地 `_by_name` 兜底（避免破坏 ch13 既有调用）
3. `send_message(name, message)` 优先用 `name_reg.resolve(name)`；`get_by_name` 同理
4. 新增 `on_task_done(fn: Callable[[str], Awaitable[None]])` 注册接口，可注册多个
5. `launch` / `send_message` / `adopt_running` 的 runner `finally` 末尾（`_push_done` 之后）逐个 `await` 调 `on_task_done` 回调（异常被 `BaseException` 兜底不中断）

**验证:** 单测：注册 `on_task_done`，`launch` 一个 noop task，等完成，断言回调被触发

## T22: 协作工具实现 — `src/novacode/team/tools/`

**依赖:** T3, T6, T7, T8
**步骤:**
1. 每个工具实现 `tool.Tool` Protocol（`name`/`description`/`parameters`/`read_only`/`execute`）
2. `TeamCreate`（F21）：参数 `team_name` + `description` + `agent_type`；`execute` 调 `await manager.create`，返回 JSON
3. `TeamDelete`（F23）：参数 `team_name` + `force`；`execute` 调 `await manager.delete`
4. `TeamTaskCreate`（F26）：参数 `title`/`description`/`assignee`/`blocked_by`；从 ctx 取 TeammateContext 找当前 Team；`execute` 调 `await store.create`
5. `TeamTaskGet`（F27）：参数 `task_id`
6. `TeamTaskList`（F28）：参数 `status` 过滤；返回带 `is_ready` 字段的 JSON 数组
7. `TeamTaskUpdate`（F29）：参数 `task_id` + 各 Patch 字段
8. `TeamSendMessage`（F34）：参数 `to`/`summary`/`message`/`type`/`payload`；`execute` 调 `await mailbox.write` + `await backend.wake` + 续派检测（T31）
9. 每个工具 `read_only`：TeamCreate/Delete/TeamTaskCreate/TeamTaskUpdate/TeamSendMessage 返回 False；TeamTaskGet/TeamTaskList 返回 True
10. 工具从 ctx 取 TeammateContext 定位 Team：队员经 ctx；Lead 走 active team（`TeamSendMessage`/`TeamTask*` 由 Lead 调时经 `manager` 内解析）

**验证:** 每个工具一个单测覆盖正常路径与错误路径

## T23: 协作工具白名单生效 — 验证

**依赖:** T17, T22
**步骤:**
1. 在 `tests/test_tool_filter.py` 中加用例:
   - 主 Agent（`exclude_tools=TEAMMATE_TOOLS`）defs 不含 `TeamTaskCreate` / `TeamSendMessage` 等
   - 普通子 Agent（`apply_agent_tool_filter` 基础）不含这 5 个
   - 队员（`filter(基础)+TEAMMATE_TOOLS`）含全部 5 个

**验证:** 测试通过

## T24: coordinator 包 — `src/novacode/coordinator/__init__.py`

**依赖:** 无
**步骤:**
1. 实现 `def is_enabled(cfg: Config) -> bool`——`cfg.features.coordinator_mode and env_truthy(os.environ.get("novaCODE_COORDINATOR_MODE", ""))`
2. 实现 `def allowed_tools() -> list[str]`（F53，含 `TeamTask*` 协作工具）
3. 实现 `def system_prompt_suffix() -> str`（F55）——四阶段框架 + "派完队员就停手等汇报"纪律段
4. 实现 `def env_truthy(v: str) -> bool`——`v.strip().lower() in {"1", "true", "yes"}`

**验证:** 单测覆盖双锁的 4 种组合（00/01/10/11），只有 11 返回 True；tmux 实跑观察 Lead 派完队员后不立刻 glob/read_file 而是"等待汇报"

## T25: config 加 features — `src/novacode/config.py`

**依赖:** 无
**步骤:**
1. 加 `@dataclass class FeaturesConfig`，字段 `coordinator_mode: bool = False` + `fork_teammate: bool = False`
2. `Config` 加字段 `features: FeaturesConfig = field(default_factory=FeaturesConfig)`
3. `_from_dict` 时若 yaml 含顶层 `features:` 段，用 `FeaturesConfig(**raw["features"])` 解析（字段名 snake_case）

**验证:** 单测加载 yaml 含 `features:` 段，断言字段被读出

## T26: TUI 集成 — `src/novacode/tui/run.py` + `src/novacode/tui/leadmail.py`

**依赖:** T3, T24, T16
**步骤:**
1. `run()` 新增参数 `team_mgr=None`、`coordinator_mode: bool = False`、`coordinator_allowed: list[str] | None = None`、`coordinator_prompt: str = ""`
2. 构造主 Agent 时：`exclude_tools=TEAMMATE_TOOLS`（恒排除协作工具）；若 `coordinator_allowed` 非 None，`allowed_tools=coordinator_allowed`；若 `coordinator_prompt`，`system_prompt` 装配时追加后缀
3. 状态栏 `status_text()` 追加 ` [COORDINATOR]`（`coordinator_mode=True` 时）；`_input_frame` 的 `status_text` 调用传 `coordinator_mode`
4. `CommandContext` 加 `team_mgr` 字段（`command/context.py`），`/team` 命令 handler 访问
5. 新建 `tui/leadmail.py`:
   - `LeadMessage` dataclass（team_name/from_/type/summary/content/ts）
   - `async def consume_lead_mail(team_mgr, runtime, lead_mail_event)`——每秒 `team_mgr.poll_lead_mailboxes()`，非空时 `build_team_update_reminder` → `runtime.append_reminders` → `event.set()`
   - `build_team_update_reminder(msgs) -> str`——`<team-update>` 块，content 截断 8000 字
6. `run()` 主循环改造（F41b）:
   - 启动 `consume_lead_mail` 后台 task（team_mgr 非 None 时）
   - `_input_frame` 里把 `app` 注册到共享 ref；同时起一个等 `lead_mail_event` 的 task，收到后 `app.exit(哨兵文本)`
   - 主循环 `_input_frame` 返回哨兵 → 走 `_autonomous_turn()`（合成 user 消息 `"[team-update] 队员发来新消息，请按 Coordinator 流程处理..."`，`conv.add_user` + `_stream_reply_cwd`），不写用户输入
   - 每轮非阻塞 `consume_lead_mail` 也负责把 reminder 推进 `pending_reminders`（Lead 在 Run 中时下一轮迭代自然取出）

**验证:** config.yaml 加 `features:\n  coordinator_mode: true`，设 `novaCODE_COORDINATOR_MODE=1` 启动，状态栏出现 `[COORDINATOR]`；tmux 实跑队员 idle 后 Lead 自动出现 `[team-update]` 行

## T27: /team slash 命令 — `src/novacode/command/builtin_team.py`

**依赖:** T3
**步骤:**
1. 实现 `handle_team(ctx, args)`（`args_handler` 签名，ch14 模式），`ctx.team_mgr` 为 None 时报错
2. 子命令:
   - `/team list`（F59）
   - `/team info <name>`（F60）
   - `/team delete <name> [--force]`（F61）
   - `/team kill <member>`（F62）
3. 在 `register_builtins` 注册 `Command("team", "管理 Agent 团队", Kind.LOCAL, args_handler=handle_team)`

**验证:** `/team list` 在 TUI 输出含已创建 Team

## T28: cli wire — `src/novacode/cli.py`

**依赖:** T1-T27
**步骤:**
1. 加 `argparse`：`--team-member` / `--team` / `--member` / `--agent-id` / `--session-dir` / `--worktree` / `--agent-type` / `--model` / `--plan-mode`；`main()` 解析后传入 `_amain`
2. 构造 `name_reg = AgentNameRegistry()`；`task_mgr.set_name_registry(name_reg)`
3. 构造 `team_mgr = team.Manager(Path.home(), root, worktree_mgr, task_mgr, name_reg)`
4. 注册 7 个新工具到 registry（TeamCreate/TeamDelete/TeamTaskCreate/TeamTaskGet/TeamTaskList/TeamTaskUpdate/TeamSendMessage）
5. `agent_tool = AgentTool(..., worktree_mgr=worktree_mgr, team_hook=team_mgr)`（team_hook=team_mgr，Manager 实现 TeamHook）
6. 注册 `on_task_done` → `team_mgr.handle_task_done`
7. 若 `coordinator.is_enabled(cfg)`：把 `coordinator_allowed` / `coordinator_prompt` 传给 `run()`
8. 若 `--team-member` flag：**所有依赖 wire 完成后**直接 `await run_team_member(args, ...)` 并 `return`，不构造 TUI（F19a）；否则继续走 TUI 路径

**验证:** `python -m novacode` 主流程能启动 TUI；`ruff check src/novacode/cli.py` 通过

## T29: run_team_member 自治循环 — `src/novacode/team/member.py`

**依赖:** T28
**步骤:**
1. 实现 `async def run_team_member(team_mgr, agent_type, member_name, agent_id, session_dir, worktree_path, plan_mode, model, ...)`:
   - `os.chdir(worktree_path)`，进程 cwd 与沙箱根指到 worktree
   - 解析角色定义（`subagent.catalog.resolve(agent_type)`），拿 system_prompt / max_turns / plan_mode_required
   - 用 `apply_agent_tool_filter(FilterParams(...)) + TEAMMATE_TOOLS` 算 allowed tools（plan_mode 时只读基础 + TEAMMATE_TOOLS）
   - 构造 provider + `agent.Agent`，**强制 `dont_ask=True`**（F39a）+ `pre_iteration_reminders=ingest_team_mailbox(tc)`
   - 注入 `<team-context>` reminder + ctx 装 `TeammateContext`（mailbox 闭包）
   - 起 stdin reader asyncio task（`loop.add_reader(sys.stdin.fileno(), ...)`）：每读一行 `wake_event.set()`
   - 进主循环（F19a）：`read_unread` → 分流（text 拼 task / plan_approval / shutdown_request）→ `run_to_completion` → 通知 Lead idle → `set_member_active(False)` → 等下一条
   - 检测 mailbox 目录消失 → 优雅退出
2. 把 `agent.Event` 流转 stdout 打印（`print_agent_event`）：`Text` 直接 print、`ToolEvent` 打 `● tool(args)`、`Done` 打横线、错误打 stderr——pane 内只读日志流

**验证:** 见 AC28 步骤 4 端到端实跑——alice pane 内显示 task 执行流，`/tmp/test_alice.txt` 落地，TeamSendMessage 后 alice 能续派

## T30: 队员空闲通知 hook — `src/novacode/team/manager.py`

**依赖:** T21, T3
**步骤:**
1. 实现 `async Manager.handle_task_done(agent_id)`:
   - 查 `registry.name_of(agent_id) → name`
   - 遍历 teams 找到该成员所属 Team
   - `await set_member_active(name, False)`（reload_from_disk_locked 保护）
   - `await mailbox.write("lead", Message(type=TEXT, summary=f"{name} idle", ...))`
2. cli 装配时 `task_mgr.on_task_done(_on_done)`（见 T28 步骤 6）

**验证:** 集成测试：in-process 后端 spawn 队员 → 自然结束 → 断言 Team.config 中 `is_active=False`、Lead mailbox 有 idle 消息

## T31: TeamSendMessage 续派检测 — `src/novacode/team/tools/team_send_message.py`

**依赖:** T22, T21
**步骤:**
1. `execute` 写完邮箱后:
   - 取目标 TeammateInfo.backend_type
   - 若 backend_type=in-process:
     - 查 `task_mgr.get(agent_id)`（agent_id 即 task_id），若 `status != Running`:
       - `await Team.set_member_active(name, True)`
       - `await task_mgr.send_message(member_name, content)` 走 ch13 续派接口
   - 若 Pane 后端:已通过 wake 唤醒，无需续派

**验证:** 单测：先 spawn → 等结束 → TeamSendMessage → 断言 task 重新 Running

## T32: Plan 审批权限切换 — `src/novacode/agent/team_mailbox.py` + `src/novacode/agent/__init__.py`

**依赖:** T20, T16
**步骤:**
1. 在 `ingest_team_mailbox` 闭包中：若有 `plan_approval_response(approve=True)`:
   - 调 `agent.set_permission_mode(Mode.DEFAULT)`
   - 调 `agent.set_allowed_tools(全量基础 + TEAMMATE_TOOLS)`（加回写工具）
   - reminder 附加「Lead 已批准计划，权限模式已切到 default，可执行计划」
   - 闭包需要持有 `agent` 引用（spawn 时注入）
2. `approve=False`:reminder 附加「Lead 驳回了计划，反馈:<feedback>。请调整后重新提交」；`_permission_mode` 仍 PLAN
3. Pane 子进程的 approve 切换由 `run_team_member` 主循环额外处理（T29）：读到 plan_approval_response 时同样 set_permission_mode + 合成续派 prompt

**验证:** 集成测试：队员以 plan 模式起步 → 收到 plan_approval_response(true) → `agent._permission_mode` 切到 DEFAULT、`_allowed_tools` 含写工具

## T33: 沙箱开放 /tmp — `src/novacode/permission/sandbox.py`

**依赖:** 无
**步骤:**
1. `sandbox_ok(root, path)` 增加白名单：解析后的目标路径以 `/tmp/` 或 `/private/tmp/` 开头（或恰好等于）时放行
2. 常量 `TMP_WHITELIST_PREFIXES = ("/tmp/", "/private/tmp/")`
3. bash 走 exec-class 权限，不受本函数影响（无需改）

**验证:** 单测 `test_sandbox_contains`：`sandbox_ok(root, "/tmp/foo.txt")` 返回 True、`sandbox_ok(root, "/etc/passwd")` 返回 False、项目根内文件仍 True

## T34: 单元测试集 — 各模块 test_*.py

**依赖:** T1-T33
**步骤:**
1. 跑 `pytest`，补失败用例
2. 跑 `ruff check src/`，修警告
3. 跑 `ruff format --check src/` 看无未格式化文件
4. 可选：`mypy src/novacode/team/` 全绿

**验证:** 全绿

## T35: tmux 实跑端到端验证（WSL tmux 会话内）

**依赖:** T1-T34
**步骤:**
1. 启动 tmux:`tmux new-session -s ch15-test`
2. 在内层跑 `cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode`
3. 在 TUI 输入:「创建一个名为 demo 的团队」
4. 观察:Agent 调 TeamCreate；`~/.novacode/teams/demo/config.json` 落地；确认成功
5. 在 TUI 输入:「派 alice 用 general-purpose，在 worktree 里 echo hello > /tmp/test_alice.txt」
6. 观察:tmux split 出新 pane；alice pane 内显示日志流；`.novacode/worktrees/team-demo+alice/` 创建；`/tmp/test_alice.txt` 内容为 `hello`
7. 在 TUI 输入:`/team info demo`，确认 alice 出现
8. 在 TUI 输入:「给 alice 发消息，让她再写一行 world」（Agent 调 TeamSendMessage）
9. 观察:alice pane 被唤醒，`/tmp/test_alice.txt` 多一行 `world`
10. `/team delete demo --force`，清理

**验证:** 步骤全部成功

## T36: in-process 实跑端到端验证

**依赖:** T1-T34
**步骤:**
1. `unset TMUX TERM_PROGRAM`
2. `cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode`
3. Agent 调 TeamCreate("inproc") → backend 为 in-process
4. Agent 派 bob（后端 in-process）
5. bob 在同进程跑完
6. 观察 `team.config.json` 中 bob 的 `is_active=False`、Lead mailbox 有 idle 消息、TUI 自动出现 `[team-update]` 行
7. Lead 调 TeamSendMessage(to="bob", message="再做一件事")，bob 从 session 恢复继续

**验证:** 全部成功

## 执行顺序

```
T1 → T2 → T3 → T3b → T4
              ↘
T5 → T6        T8
T7
T9 → T10
   → T11, T12, T13（并行）
T14
T15 → T16 → T17 → T18 → T19
                → T20 → T32
T21
T22 → T23 → T31
T24 → T25 → T26
T27
T28 → T29 → T30
T33（沙箱，可与 T1 并行）
T34（收尾测试）
T35, T36（实跑验收）
```

并行机会:T5/T7/T8 互不依赖；T11/T12/T13 互不依赖；T22 中 7 个工具可分批；T33 独立。
