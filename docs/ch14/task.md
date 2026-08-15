# Worktree 隔离 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/novacode/worktree/__init__.py` | 公开导出 Manager / validate_slug / 错误类型 |
| 新建 | `src/novacode/worktree/slug.py` | validate_slug + flat_slug |
| 新建 | `tests/test_worktree_slug.py` | Slug 校验单测 |
| 新建 | `src/novacode/worktree/session.py` | WorktreeSession + JSON 原子持久化 |
| 新建 | `src/novacode/worktree/git.py` | _run_git + _has_worktree_changes + _resolve_head_sha_from_fs |
| 新建 | `tests/test_worktree_git.py` | git helper 单测 |
| 新建 | `src/novacode/worktree/manager.py` | Manager 类型 + 构造 + list/get/current_session |
| 新建 | `src/novacode/worktree/create.py` | create + 快速恢复 + post-creation setup A/B/C/D |
| 新建 | `tests/test_worktree_create.py` | create + setup 单测 |
| 新建 | `src/novacode/worktree/lifecycle.py` | enter / exit / remove / auto_cleanup |
| 新建 | `tests/test_worktree_lifecycle.py` | 生命周期单测 |
| 新建 | `src/novacode/worktree/sweep.py` | sweep_stale + 三层过滤 |
| 新建 | `tests/test_worktree_sweep.py` | sweep_stale 单测 |
| 新建 | `tests/test_worktree_manager.py` | Manager 构造 + session 持久化测试 |
| 新建 | `src/novacode/tool/ctx.py` | with_cwd / cwd_from_ctx / resolve_path |
| 新建 | `tests/test_tool_ctx.py` | resolve_path 单测 |
| 修改 | `src/novacode/tool/read_file.py` | 用 resolve_path 解析 path |
| 修改 | `src/novacode/tool/write_file.py` | 用 resolve_path 解析 path |
| 修改 | `src/novacode/tool/edit_file.py` | 用 resolve_path 解析 path |
| 修改 | `src/novacode/tool/glob_tool.py` | 用 resolve_path 解析 path（注意文件名，非 glob.py） |
| 修改 | `src/novacode/tool/grep_tool.py` | 用 resolve_path 解析 path（注意文件名，非 grep.py） |
| 修改 | `src/novacode/tool/bash.py` | 子进程 cwd=resolve_path("") |
| 修改 | `src/novacode/subagent/definition.py` | Definition 加 isolation 字段 |
| 修改 | `src/novacode/subagent/parser.py` | 解析 isolation: frontmatter（小写键） |
| 修改 | `tests/test_subagent_parser.py` | 增加 isolation 单测 |
| 新建 | `src/novacode/agent/agent_worktree.py` | _execute_with_worktree + build_worktree_notice |
| 修改 | `src/novacode/agent/agent_tool.py` | 增加 worktree_mgr 字段 + isolation 分支（强制前台） |
| 新建 | `tests/test_agent_worktree.py` | _execute_with_worktree 单测(用 stub Manager) |
| 修改 | `src/novacode/command/command.py` | Command 加 args_handler 字段 |
| 修改 | `src/novacode/command/dispatch.py` | parse 改三元组 (name, tail, is_slash) + dispatch_slash 透传 tail |
| 修改 | `src/novacode/command/context.py` | CommandContext 加 worktree_mgr / set_active_cwd 字段 |
| 新建 | `src/novacode/command/builtin_worktree.py` | /worktree handler + 子命令解析（经 CommandContext） |
| 修改 | `src/novacode/command/builtins.py` | 注册 /worktree（args_handler） |
| 修改 | `tests/test_command_dispatch.py` | 更新 test_parse 三元组断言（ch10 F7 行为修订） |
| 修改 | `tests/test_command_builtins.py` | 加 worktree 注册/分发测试 |
| 修改 | `src/novacode/tui/run.py` | worktree_mgr 参数 / active_cwd_box / 注入 ctx |
| 修改 | `src/novacode/cli.py` | 构造 Manager + 注入 AgentTool / run + sweep_stale |
| 修改 | `.gitignore` | 追加 .novacode/worktrees/ + worktree_session.json |

> 注：**不新建 `command/ui.py`（WorktreeAccessor）与 `tui/worktree_adapter.py`**——ch10 已废弃 UI Protocol 设想，命令经 CommandContext 访问能力。

## T1: Slug 校验

**文件:** `src/novacode/worktree/slug.py` + `tests/test_worktree_slug.py`
**依赖:** 无
**步骤:**
1. 创建子包 `novacode.worktree`，加 `__init__.py`（暂时空导出，后续 T 步骤补）
2. 实现 `validate_slug(name: str) -> None`，规则:非空、长度 ≤ 64、按 `/` 切段后每段匹配 `^[a-zA-Z0-9._-]+$` 且不能是 `.` 或 `..`、无连续 `//`、无首末 `/`;失败抛 `ValueError(<具体原因>)`
3. 实现 `flat_slug(name: str) -> str`:`name.replace("/", "+")`
4. 写测试覆盖合法/非法 case:`alice`、`team/alice`、`v1.0`、`a_b`(合法);空、超长、`..`、`./x`、`a//b`、`/x`、`a/`、`a b`、`a;b`(非法,断言 `pytest.raises(ValueError)`)

**验证:** `pytest tests/test_worktree_slug.py -v`

## T2: WorktreeSession 持久化

**文件:** `src/novacode/worktree/session.py`
**依赖:** T1
**步骤:**
1. 定义 `WorktreeSession` dataclass，字段按 spec F3，JSON 序列化用 `dataclasses.asdict + json.dumps`
2. 实现 `load_session(path: Path) -> WorktreeSession | None`:文件不存在返回 None;内容为 `null` 或空返回 None;JSON 解析失败抛异常（Manager 构造层 catch 后警告清空）
3. 实现 `save_session(path: Path, session: WorktreeSession | None) -> None`:session=None 时写 `null`;原子写——先写 `path.with_suffix(path.suffix + ".tmp")` 再 `os.replace`
4. 实现 `clear_session(path: Path)`(等同 `save_session(path, None)`)

**验证:** 在 test_worktree_manager.py T4 中覆盖

## T3: Git helper

**文件:** `src/novacode/worktree/git.py` + `tests/test_worktree_git.py`
**依赖:** 无
**步骤:**
1. 实现 `async def _run_git(work_dir: str, *args: str) -> str`:用 `asyncio.create_subprocess_exec("git", *args, cwd=work_dir, env=..., stdin=DEVNULL, stdout=PIPE, stderr=PIPE)`，env 注入 `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=""`(在 `os.environ` 副本基础上)，`await proc.communicate()`，返回 stdout decode 并 rstrip 换行;失败抛 `RuntimeError(stderr)`
2. 实现 `async def _has_worktree_changes(wt_path: str, base_commit: str) -> bool`:① `git status --porcelain` 非空 ② `git rev-list --count <base_commit>..HEAD` >0;任一 git 命令本身出错 fail-closed 返回 True（base_commit 为空串时 rev-list 会报错 → 返回 True，安全）
3. 实现 `_resolve_head_sha_from_fs(wt_path: str) -> str | None`（纯 fs，不调 git）:
   - worktree 里 `.git` 是**文件**（内容 `gitdir: <私有 gitdir 路径>\n`，不是目录）；读它取 gitdir
   - 读 `<gitdir>/HEAD`:若是 `ref: refs/heads/<name>`，先在 `<gitdir>/refs/heads/<name>` 读 SHA，失败再回退到 common gitdir（读 `<gitdir>/commondir` 文件，缺省 `..`，即 `<repo_root>/.git`）的 `<common>/refs/heads/<name>`；HEAD 若是直接 SHA 则用它
   - 任何一步失败返回 None（head_commit 置空，变更检查 fail-closed 兜底）
4. 测试:用一个临时 git 仓库做真实 Worktree（`git worktree add` 准备 fixture），断言上述函数行为（可用 `pytest.fixture` + `subprocess.run` 准备 fixture）

**验证:** `pytest tests/test_worktree_git.py -v`

## T4: Manager 构造

**文件:** `src/novacode/worktree/manager.py` + `tests/test_worktree_manager.py`
**依赖:** T2, T3
**步骤:**
1. 定义 `Manager` 类(spec F4 字段) + 模块常量 `DEFAULT_SYMLINK_DIRS = ["node_modules", ".venv", "vendor"]`
2. 实现 `Manager.__init__(self, repo_root: str)`:
   - `self.repo_root = str(Path(repo_root).resolve())`
   - 同步跑 `subprocess.run(["git", "-C", repo_root, "rev-parse", "--show-toplevel"], capture_output=True, text=True)`，输出与 repo_root 不匹配则抛 `ValueError("not a git repo root")`
   - 初始化 `worktree_dir`、`session_file`、`active = {}`、`lock = asyncio.Lock()`、`symlink_dirs = DEFAULT_SYMLINK_DIRS`
   - `Path(worktree_dir).mkdir(parents=True, exist_ok=True)`
   - 调 `load_session(session_file)`;若 session 非 None 但其 worktree_path 不存在，清空 session 并 stderr 警告；文件非法 JSON 同样警告清空（N5）
   - 扫描 `worktree_dir` 子目录，对每个非空目录用 `_resolve_head_sha_from_fs` 填 `active`（快速恢复路径，不调 git）
3. 实现 `list() -> list[Worktree]`(按 name 排序)、`get(name) -> Worktree | None`、`current_session() -> WorktreeSession | None`
4. 测试:在临时 git 仓库构造 Manager，断言 worktree_dir 创建、空 session 时 current_session()=None、预放 session 文件能被加载、Worktree 目录不存在时 session 被清空

**验证:** `pytest tests/test_worktree_manager.py -v`

## T5: create + 快速恢复 + 创建后设置

**文件:** `src/novacode/worktree/create.py` + `tests/test_worktree_create.py`
**依赖:** T4
**步骤:**
1. 实现 `async def create(self, name, base_ref, manual) -> Worktree`(挂在 Manager 上，可用 mixin 或 import 后绑定):
   - `validate_slug(name)` 不通过即抛 ValueError
   - `async with self.lock:`;`active[name]` 存在即抛 ValueError
   - 算 `flat = flat_slug(name)`、`wt_path = self.worktree_dir / flat`、`branch = f"worktree-{flat}"`
   - 若 `wt_path.exists()`，用 `_resolve_head_sha_from_fs` 取 sha，构造 Worktree 放 active，**直接返回**(快速恢复，跳过 setup，不调 `git worktree add`;sha 取不到时 head_commit 置空串)
   - 否则跑 `await _run_git(self.repo_root, "worktree", "add", "-B", branch, str(wt_path), base_ref)`
   - 失败时:`shutil.rmtree(wt_path, ignore_errors=True)`，重新抛异常
   - 调 `await _perform_post_creation_setup(self.repo_root, wt_path, self.symlink_dirs)`，内部每个子步骤 try/except 仅 stderr 警告
   - 跑 `head_sha = await _run_git(wt_path, "rev-parse", "HEAD")` 拿 head SHA
   - 构造 Worktree(`name, path=str(wt_path), branch, based_on=base_ref, head_commit=head_sha, created=datetime.now(), manual`)放 active，返回
2. 实现 `async def _perform_post_creation_setup(repo_root, wt_path, symlink_dirs)`:四个子函数:
   - `copy_local_configs(repo_root, wt_path)`:对 `.novacode/config.yaml` / `.novacode/settings.local.yaml`，若主仓存在且 Worktree 不存在，`shutil.copy`
   - `setup_git_hooks(repo_root, wt_path)`:优先 `.husky/`，回退 `git -C <repo_root> config --get core.hooksPath` 拿主仓配置，若有值跑 `git -C <wt_path> config core.hooksPath <绝对路径>`
   - `symlink_large_dirs(repo_root, wt_path, symlink_dirs)`:对每个目录若主仓存在且 Worktree 不存在，`os.symlink(Path(repo_root)/dir, Path(wt_path)/dir, target_is_directory=True)`（Windows 需要该参数，POSIX 忽略）
   - `copy_included_ignored(repo_root, wt_path)`:读 `.worktreeinclude` 模式;跑 `git -C <repo_root> ls-files --others --ignored --exclude-standard --directory` 列出忽略文件;每个文件用 `fnmatch.fnmatch` 对模式;命中则 `shutil.copy` 到 Worktree
   - 每个子函数 try/except 失败只往 stderr 写一行 `worktree: setup <step>: <err>` 警告，继续下个步骤
3. 测试:在临时 git 仓库覆盖:create 成功后目录存在、分支存在、设置 A 复制 settings.local.yaml、设置 C 软链 node_modules、设置 D 按 .worktreeinclude 复制 .env;快速恢复路径不调 `git worktree add`(monkeypatch `_run_git` 断言 `worktree add` 分支未触发)

**验证:** `pytest tests/test_worktree_create.py -v`

## T6: enter / exit / remove / auto_cleanup

**文件:** `src/novacode/worktree/lifecycle.py` + `tests/test_worktree_lifecycle.py`
**依赖:** T5
**步骤:**
1. 实现 `ExitAction`(str Enum)、`ExitOptions`、`ExitReport`、`AutoCleanupReport` 类型与 `WorktreeHasChangesError`
2. 实现 `async def enter(self, name) -> WorktreeSession`:
   - `async with self.lock:`，取 `active[name]`
   - `original_cwd = str(Path.cwd())`
   - `original_branch = await _run_git(self.repo_root, "rev-parse", "--abbrev-ref", "HEAD")` 与 `original_head = await _run_git(self.repo_root, "rev-parse", "HEAD")`(try/except 失败用空字符串兜底)
   - 生成 `session_id = secrets.token_hex(8)`(保证唯一)
   - 写 `self.current_session` 字段，持久化 `save_session`
3. 实现 `async def exit(self, name, action, opts) -> ExitReport`:
   - `async with self.lock:`;校验 `current_session` 非空且 `worktree_name == name`;否则抛 ValueError
   - 取 `active[name]`;若 None 抛 ValueError
   - `action=REMOVE` 且 `not opts.discard_changes`:调 `_has_worktree_changes`，True 则抛 `WorktreeHasChangesError`
   - `os.chdir(current_session.original_cwd)` 兜底(`contextlib.suppress(OSError)` 包住)
   - `self.current_session = None`,`save_session(session_file, None)`
   - `action=REMOVE`:`await _run_git(self.repo_root, "worktree", "remove", "--force", wt.path)`,`await asyncio.sleep(0.1)`,`await _run_git(self.repo_root, "branch", "-D", wt.branch)`,`del active[name]`
   - 返回 `ExitReport(removed=action==REMOVE, path=wt.path, branch=wt.branch)`
4. 实现 `async def remove(self, name, opts)`:类似 exit 的 remove 分支，但允许非当前 session;变更保护同
5. 实现 `async def auto_cleanup(self, name) -> AutoCleanupReport`:
   - 取 `active[name]`;`manual=True` 直接 `AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)`
   - `_has_worktree_changes` 为 False 调 `remove(name, ExitOptions(discard_changes=True))`，返回 `AutoCleanupReport(kept=False)`
   - 有变更:`AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)`
6. 测试:enter 不改进程 cwd、exit 切回 cwd、exit remove 变更保护、auto_cleanup manual/无变更/有变更三种分支(用 `pytest.mark.asyncio`)

**验证:** `pytest tests/test_worktree_lifecycle.py -v`

## T7: sweep_stale

**文件:** `src/novacode/worktree/sweep.py` + `tests/test_worktree_sweep.py`
**依赖:** T6
**步骤:**
1. 在 sweep.py 定义 `EPHEMERAL_PATTERN = re.compile(r"^agent-a[0-9a-f]{7}$")`
2. 实现 `async def sweep_stale(self, cutoff: datetime) -> list[str]`:
   - 遍历 `Path(self.worktree_dir).iterdir()`
   - 对每个目录:不匹配 pattern 跳过;`datetime.fromtimestamp(p.stat().st_mtime) > cutoff` 跳过;`current_session.worktree_path == str(p)` 跳过
   - 变更检查（fail-closed，不依赖 head_commit）:① `git status --porcelain` 非空跳过 ② `git rev-list --max-count=1 HEAD --not --remotes` 非空跳过（有未推送 commit 保留）
   - 通过的:调 `remove(name, ExitOptions(discard_changes=True))`，记 removed.append(name)
3. 实现 `random_agent_name() -> str`(返回 `"agent-a" + secrets.token_hex(4)[:7]`)
4. 测试:构造三个目录(匹配模式无变更、匹配模式有变更、不匹配模式)，sweep_stale 只删第一个

**验证:** `pytest tests/test_worktree_sweep.py -v`

## T8: tool ctx

**文件:** `src/novacode/tool/ctx.py` + `tests/test_tool_ctx.py`
**依赖:** 无(并行 T1-T7)
**步骤:**
1. 在 `src/novacode/tool/ctx.py` 定义 `_ctx_cwd: ContextVar[str | None] = ContextVar("cwd", default=None)`
2. 实现 `with_cwd(directory: str)` 作为 `@contextmanager`:`directory==""` 时直接 yield 不变;否则 `token = _ctx_cwd.set(directory); try yield finally _ctx_cwd.reset(token)`
3. 实现 `cwd_from_ctx() -> str | None`(返回 `_ctx_cwd.get()`)
4. 实现 `resolve_path(p: str) -> str`:
   - `base = _ctx_cwd.get() or str(Path.cwd())`
   - `p` 为空:返回 base
   - `Path(p).is_absolute()`:返回 `str(Path(p))`
   - 否则:返回 `str(Path(base) / p)`
5. 测试:覆盖三种 path、ctx 无 cwd 时回落到进程 cwd、空字符串返回 cwd 本身

**验证:** `pytest tests/test_tool_ctx.py -v`

## T9: 改造 6 个核心工具

**文件:** `src/novacode/tool/{bash,read_file,write_file,edit_file,glob_tool,grep_tool}.py`
**依赖:** T8
**步骤:**
1. `read_file.py`:在 `Path(args.path).stat()` / `read_text()` 前 `abs_path = resolve_path(args.path)`，后续用 `Path(abs_path)`
2. `write_file.py`:同样改造 path 参数;若需要 `mkdir(parents=True)` 时也用 abs
3. `edit_file.py`:同样
4. `glob_tool.py`:`path = data.get("path") or "."`;然后 `abs_root = resolve_path(path)`;`Path(abs_root).rglob(...)` 用 abs root;返回路径仍按相对 root 输出(保持现有行为)
5. `grep_tool.py`:与 glob_tool 同
6. `bash.py`:在 `asyncio.create_subprocess_shell` 调用上设 `cwd=resolve_path("")`(空字符串解析为 cwd 本身)
7. 不改 schema(`Tool.parameters` 不变)，不改 description
8. 单测:构造 `with with_cwd(tmp_dir):` 到临时目录，在临时目录里准备文件，调工具断言读到对应内容（`tests/test_tool.py` 内加 cwd 用例）

**验证:** `pytest tests/test_tool.py tests/test_tool_bash.py -v`

## T10: subagent.Definition.isolation

**文件:** `src/novacode/subagent/{definition,parser}.py` + `tests/test_subagent_parser.py`
**依赖:** 无
**步骤:**
1. `definition.py`:`Definition` dataclass 加 `isolation: str = ""` 字段
2. `parser.py`:`_from_meta` 里 `raw = str(fm.get("isolation") or "").strip()`，合法值 `""` / `"worktree"`，非法值 stderr 警告并回落 `""`，把结果填到 `definition.isolation`
3. `tests/test_subagent_parser.py`:增加测试覆盖 `isolation: worktree` 解析成功、`isolation: gibberish` 警告并回落空(用 `capsys` 断言 stderr 内容)

**验证:** `pytest tests/test_subagent_parser.py -v`

## T11: _execute_with_worktree

**文件:** `src/novacode/agent/agent_worktree.py` + `tests/test_agent_worktree.py`
**依赖:** T6, T8, T10
**步骤:**
1. 新建 `agent_worktree.py`，顶部 `from novacode.worktree import Manager, random_agent_name`(worktree 包不依赖 agent，无导入循环)
2. 实现 `build_worktree_notice(parent_cwd: str, wt_path: str) -> str`(按 spec F22 模板)
3. 实现 `async def _execute_with_worktree(manager: Manager, definition, sub_agent, sub_conv, prompt: str, events) -> str`:
   - `name = random_agent_name()`
   - `wt = await manager.create(name, "HEAD", manual=False)`
   - `cwd = str(Path.cwd())`
   - `notice = build_worktree_notice(cwd, wt.path)`
   - `task_text = notice + "\n\n" + prompt`
   - `with with_cwd(wt.path):`
   - `    final_text = await sub_agent.run_to_completion(sub_conv, task_text, events)`
   - `report = await manager.auto_cleanup(name)`
   - 若 `report.kept`，把 `\n[Worktree 保留: <path>, 分支 <branch>]` 追加到 `final_text`
   - 返回 `final_text`
4. 单测:用一个真实临时 git 仓库构造 worktree.Manager;sub_agent 用 mock provider(返回空文本即结束);断言 wt.path 被传到 ctx(可在 run_to_completion 内打桩读 cwd_from_ctx)、auto_cleanup 被调用

**验证:** `pytest tests/test_agent_worktree.py -v`

## T12: AgentTool 接入 isolation 分支

**文件:** `src/novacode/agent/agent_tool.py`
**依赖:** T11
**步骤:**
1. AgentTool 加属性 `worktree_mgr: Manager | None`
2. `__init__(self, catalog, task_mgr, parent=None, bg_enabled=True, worktree_mgr=None)`——签名末尾追加 worktree_mgr(允许 None 表示不启用)
3. 在 execute 内计算 `background = defi.background or a_args.run_in_background or defi.is_fork()` 之后、`if background and not self._bg_enabled` 检查之前，加:
   ```python
   if defi.isolation == "worktree":
       background = False  # isolation 时强制前台（忽略 background 字段与 run_in_background）
   ```
4. 若 `self.worktree_mgr is None` 且 `definition.isolation == "worktree"`，返回 `Result(is_error=True, content="worktree manager not configured")`
5. 在前台 inline 路径中，若 `definition.isolation == "worktree"`，调 `_execute_with_worktree(self.worktree_mgr, definition, sub_agent, sub_conv, a_args.prompt, events)` 替代直接 `run_to_completion`，返回 `Result(content=final_text)`（`_execute_with_worktree` 返回 str）
6. 改 `src/novacode/cli.py` 的 `AgentTool` 构造调用，传入 `worktree_mgr`

**验证:** `pytest tests/test_agent_tool.py tests/test_agent_worktree.py -v`

## T13: 命令带参数机制 + /worktree handler

**文件:** `src/novacode/command/{command,dispatch,context}.py` + `src/novacode/command/builtin_worktree.py` + `src/novacode/command/builtins.py` + `tests/test_command_dispatch.py` + `tests/test_command_builtins.py`
**依赖:** T6
**步骤:**
1. `command.py`:`Command` dataclass 加可选字段 `args_handler: Callable[[CommandContext, str], Awaitable[None]] | None = None`（handler 与 args_handler 互斥用其一，dispatch 优先 args_handler）
2. `dispatch.py`:
   - `parse` 改返回三元组 `(name, tail, is_slash)`：非斜杠 `("", "", False)`；`/help` → `("help", "", True)`；`/help xx` → `("help", "xx", True)`（tail 已 strip）；`/ /help` → `("", "", True)`。**注意这是对 ch10 F7「零参数」的有意修订**，需同步更新 `tests/test_command_dispatch.py` 的 `test_parse` 断言与 ch10 checklist F7 验收
   - `dispatch_slash`:命中命令后 `if cmd.args_handler is not None: await cmd.args_handler(ctx, tail) else: await cmd.handler(ctx)`;其余逻辑（lookup miss 提示、异常兜底）不变
3. `context.py`:`CommandContext` 加字段 `worktree_mgr: Any = None`、`set_active_cwd: Any = None`（duck typing，command 包不 import worktree/tui）
4. `builtin_worktree.py`:实现 `async def handle_worktree(ctx: CommandContext, args: str) -> None`——args 是 `/worktree` 后面的全部尾随字符串（已 strip）;split 子命令 + 其余参数:
   - `create <slug>` → `await ctx.worktree_mgr.create(slug, "HEAD", manual=True)`，输出 `Worktree 已创建: <path> (分支 <branch>)`
   - `list` → 遍历 `list()`，每行 `<name>  <path>  <branch>  [manual]`（当前 session 的 wt 附加 `[active]`）
   - `enter <slug>` → `await enter(slug)`，调 `ctx.set_active_cwd(session.worktree_path)`，输出 `已进入 <slug>: <path>`
   - `exit [--remove] [--discard]` → 解析 flag，调 `exit`
   - `remove <slug> [--discard]` → 调 `remove`
   - 未知子命令报错;`ctx.worktree_mgr is None` 时报错「Worktree 未启用」
5. `builtins.py`:注册 `Command(name="worktree", description="管理 Worktree 隔离副本", kind=Kind.UI, args_handler=handle_worktree)`
6. 测试:
   - `test_command_dispatch.py`:更新 `test_parse` 断言为三元组
   - `test_command_builtins.py`:注册命中测试 + handle_worktree 分发逻辑（用 stub CommandContext：worktree_mgr 用真 Manager 或 stub、set_active_cwd 记参数）

**验证:** `pytest tests/test_command_dispatch.py tests/test_command_builtins.py -v`

## T14: tui/run.py 注入 ctx cwd

**文件:** `src/novacode/tui/run.py`
**依赖:** T11, T13
**步骤:**
1. `run()` 加参数 `worktree_mgr=None`（ch14）
2. 主循环内加可变容器 `active_cwd_box: list[str] = [""]`（空=进程 cwd），定义:
   - `def _set_active_cwd(cwd: str) -> None: active_cwd_box[0] = cwd`
   - `def _effective_cwd() -> str: return active_cwd_box[0] or str(Path.cwd())`
3. 构造 CommandContext 时传 `worktree_mgr=worktree_mgr`、`set_active_cwd=_set_active_cwd`
4. 主 Agent 每次 Run 前包 ctx cwd——封装 helper 并用于**两处** `_stream_reply` 调用（主循环对话与 `run_callback`）:
   ```python
   async def _stream_reply_cwd(agent, conv, console, mode, cancel):
       with with_cwd(_effective_cwd()):
           return await _stream_reply(agent, conv, console, mode, cancel)
   ```
5. 启动装配:若 `worktree_mgr` 非 None 且 `manager.current_session()` 非空，`active_cwd_box[0] = session.worktree_path`

**验证:** `python -m novacode` 可启动;`/worktree create x` + `/worktree enter x` + Read file(相对路径) 在 worktree 内成功

## T15: 主 cli 接入

**文件:** `src/novacode/cli.py` + `.gitignore`
**依赖:** T4-T14 全部
**步骤:**
1. `cli.py`:在 `subagent_catalog = subagent.load_catalog(root)` 后加:
   ```python
   try:
       worktree_mgr = worktree.Manager(root)
   except Exception as exc:
       print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
       worktree_mgr = None
   else:
       asyncio.create_task(
           worktree_mgr.sweep_stale(datetime.now() - timedelta(hours=24))
       )
   ```
   （`_amain` 是 async 函数，用 `asyncio.create_task` 而非 `get_event_loop()`）
2. `AgentTool` 构造末尾追加 `worktree_mgr=worktree_mgr`，`bg_enabled=cfg.effective_enable_subagent_background()`
3. `run(...)` 调用新增 `worktree_mgr=worktree_mgr`
4. `.gitignore` 追加:
   ```
   # ch14: Worktree 隔离副本(仅供 SubAgent 与手动管理使用)
   .novacode/worktrees/
   .novacode/worktree_session.json
   ```

**验证:** `python -m novacode` 可启动、`pytest` 全过、`ruff check` 通过

## T16: 端到端 tmux 验证（WSL）

**文件:** 无代码修改，运行测试
**依赖:** T15
**步骤:**
1. `uv sync` 装好依赖(或 `pip install -e .`)
2. 准备项目级自定义 Agent `.novacode/agents/worktree-writer.md`(详见 checklist 场景 1，注意 frontmatter 用 camelCase 键 `permissionMode`)
3. WSL tmux 启动 `python -m novacode`，跑 checklist 端到端场景（WSL tmux 流程参照 ch13：`wsl -d Ubuntu -- bash -lc 'cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode'`，capture-pane 输出写文件后从 Windows 侧 Read，启动后 sleep 12s）
4. 通过即标记 T16 完成

**验证:** 见 checklist.md 场景 1-6

## 执行顺序

```
T1 (slug)
  ↓
T2 (session) — T3 (git helper) — T8 (tool/ctx)
                                    ↓
T4 (manager construct)          T9 (改造 6 tools)
  ↓
T5 (create + setup)
  ↓
T6 (lifecycle)
  ↓
T7 (sweep)
  ↓
T10 (subagent.isolation)
  ↓
T11 (agent_worktree + _execute_with_worktree)
  ↓
T12 (AgentTool 接入)
  ↓
T13 (/worktree command + parse 三元组) — T14 (tui/run.py 注入)
                              ↓
T15 (cli.py + .gitignore)
  ↓
T16 (WSL tmux 端到端)
```

T1/T2/T3/T8 之间可并行;其余按依赖顺序。
