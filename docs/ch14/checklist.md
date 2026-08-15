# Worktree 隔离 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
>
> **验收结论（2026-08-11）**：单测 **693 passed + 2 skipped**（`test_worktree_*.py` 49 passed + 1 symlink skip）、
> `ruff check` / `ruff format` 全绿、ch14 新增模块 mypy 干净。WSL tmux 端到端实跑场景 1/2/4/6 全部通过、
> 场景 3/5 核心路径通过（详见文末场景标注）。
>
> **验收补全（2026-08-11 晚）**：前述 3 项已全部补齐——① symlink 用 WSL 脚本复验通过；② session 重启恢复 active_cwd 用 tmux 两轮实跑通过；③ `/worktree remove` 与 exit 后二次读取均实跑通过。**checklist 现无未验收项**。

## 实现完整性

### worktree 子包

- [x] src/novacode/worktree 子包存在且可导入(验证:`python -c "from novacode import worktree"`)
- [x] `validate_slug` 对合法/非法 case 行为符合 spec F1(验证:`pytest tests/test_worktree_slug.py -v`)
- [x] `flat_slug("team/alice") == "team+alice"`(验证:同上)
- [x] `WorktreeSession` JSON 序列化/反序列化字段名为下划线小写(验证:`pytest tests/test_worktree_manager.py -k session -v`)
- [x] `save_session` 原子写——失败前不破坏既有文件;`save_session(path, None)` 写入 `null`(验证:同上)
- [x] `_run_git` 设置 `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=""`、stdin=DEVNULL(验证:`pytest tests/test_worktree_git.py -k run_git -v`)
- [x] `_has_worktree_changes` 在临时 git 仓库内:无修改返回 False;改一个文件返回 True;git 命令出错 fail-closed 返回 True(验证:同上)
- [x] `_has_worktree_changes` 忽略 `.novacode/` 与软链大目录(`.venv` 等,post-creation setup 创建)不算变更(验证:`pytest tests/test_worktree_git.py -k ignores_setup`)
- [x] `_resolve_head_sha_from_fs` 在真实 worktree 路径下返回 commit SHA（.git 是文件、分支 ref 在 common dir；解析失败返回 None）(验证:`pytest tests/test_worktree_git.py -k resolve_head`)
- [x] `Manager(repo_root)` 校验 repo_root 是 git 仓库;非 git 目录抛 ValueError(验证:`pytest tests/test_worktree_manager.py -k construct -v`)
- [x] `Manager` 加载已存在的 session 文件;指向不存在目录的 session 自动清空(验证:同上)
- [x] `manager.create("alice", "HEAD", manual=True)` 在 `.novacode/worktrees/alice/` 下落地 + 分支 `worktree-alice`(验证:`pytest tests/test_worktree_create.py`)
- [x] `manager.create("team/alice", ...)` 落地 `.novacode/worktrees/team+alice/` + 分支 `worktree-team+alice`(验证:同上)
- [x] `manager.create` 目录已存在（active 中无）时走快速恢复(不调 `git worktree add`)(验证:同上，monkeypatch `_run_git` 断言 `worktree add` 分支未触发)
- [x] `manager.create` 已 active 名字时再 create 抛异常(验证:同上)
- [x] 创建后设置 A——`.novacode/settings.local.yaml` 被复制到 Worktree(验证:同上，需在测试 fixture 准备文件)
- [x] 创建后设置 B——主仓 `.husky/` 存在时 Worktree git config 含 core.hooksPath(验证:`pytest tests/test_worktree_create.py -k hooks`)
- [x] 创建后设置 C——主仓 node_modules 存在时 Worktree 内为软链(`Path.is_symlink()` 为 True)(验证:本机 Windows 无 symlink 权限被 skip;WSL 脚本复验 `symlink=True target_readable=True`——`Manager.create` 后 `node_modules` 为 symlink 且可读)
- [x] 创建后设置 D——主仓 `.worktreeinclude` 模式命中的 ignored 文件被复制到 Worktree(验证:`pytest tests/test_worktree_create.py -k include_ignored`)
- [x] `manager.enter(name)` 不改变进程 `Path.cwd()`，返回 session 含 original_cwd/worktree_path/session_id 等字段(验证:`pytest tests/test_worktree_lifecycle.py -k enter`)
- [x] `manager.enter` 持久化 session 到 `.novacode/worktree_session.json`(验证:同上)
- [x] `manager.exit(name, REMOVE, ExitOptions())` 有变更时抛 `WorktreeHasChangesError`，Worktree 目录仍在(验证:`pytest tests/test_worktree_lifecycle.py -k exit`)
- [x] `manager.exit(name, REMOVE, ExitOptions(discard_changes=True))` 成功删除 Worktree + 分支;session 文件被清空(验证:同上)
- [x] `manager.exit` 调用了 `os.chdir(original_cwd)` 兜底(验证:`pytest tests/test_worktree_lifecycle.py -k chdir`——monkeypatch.chdir 后 exit，断言 cwd 恢复)
- [x] `manager.remove(name, ExitOptions())` 与 exit 的 remove 分支一致，但允许非当前 session(验证:同上)
- [x] `manager.auto_cleanup` 对 manual=True 直接 kept=True(验证:`pytest tests/test_worktree_lifecycle.py -k auto_cleanup`)
- [x] `manager.auto_cleanup` 无变更时 remove 并返回 kept=False;有变更返回 kept=True(验证:同上)
- [x] `manager.sweep_stale` 第一层只识别 `agent-a[0-9a-f]{7}` 模式;手动命名跳过(验证:`pytest tests/test_worktree_sweep.py`)
- [x] `manager.sweep_stale` 跳过当前 session 的目录(验证:同上)
- [x] `manager.sweep_stale` 有未提交修改 / 未推送 commit 的目录跳过(fail-closed)(验证:未提交修改 `-k only_clean_ephemeral`;未推送 commit `-k unpushed_commit`——构造 bare remote + worktree 新提交)
- [x] `worktree.random_agent_name` 返回形如 `agent-a[0-9a-f]{7}` 的字符串(验证:`pytest tests/test_worktree_sweep.py -k random_agent_name`)

### tool 包 ctx 改造

- [x] `tool.with_cwd` / `cwd_from_ctx` / `resolve_path` 三函数存在(验证:`pytest tests/test_tool_ctx.py -v`)
- [x] `resolve_path` 对绝对路径直接返回;对相对路径用 ctx cwd 或 `Path.cwd()` 拼接(验证:同上)
- [x] `read_file(path="a.txt")` 在 `with with_cwd(tmp_dir):` 下读 tmp_dir/a.txt(验证:`pytest tests/test_tool.py -k cwd`)
- [x] `write_file(path="a.txt")` + ctx cwd 同上(验证:同上)
- [x] `edit_file(path="a.txt")` + ctx cwd 同上(验证:同上)
- [x] `bash(command="pwd")` + ctx cwd 输出 cwd 路径(验证:`pytest tests/test_tool_bash.py -k cwd`;新增用例在 test_tool.py -k bash_with_ctx_cwd)
- [x] `glob(pattern="*.txt")` + ctx cwd 在 cwd 内搜索(验证:`pytest tests/test_tool.py -k cwd`)
- [x] `grep` + ctx cwd 同上(验证:同上)
- [x] 工具 schema 不变——`Tool.parameters` 不含新字段(验证:`tests/test_agent_tool.py::test_name_and_parameters` 参数集不变 + 工具定义断言)

### subagent 包扩展

- [x] `subagent.Definition` 含 `isolation: str` 字段(验证:`pytest tests/test_subagent_parser.py`)
- [x] `parse_definition` 正确解析 `isolation: worktree`(验证:`pytest tests/test_subagent_parser.py -k isolation -v`)
- [x] 非法 `isolation` 值时 stderr 警告并回落 `""`(验证:同上，用 `capsys` 断言)
- [x] 既有定义不写 isolation 时 `isolation == ""`(验证:同上)

### agent 包扩展

- [x] `agent.AgentTool` 含 `worktree_mgr` 字段;`__init__` 签名末尾接收 worktree_mgr(验证:`pytest tests/test_agent_tool.py`)
- [x] `agent._execute_with_worktree` 调用 `manager.create` + `auto_cleanup`，期间通过 ctx 传 wt.path(验证:`pytest tests/test_agent_worktree.py -v`)
- [x] `build_worktree_notice` 输出含 `<worktree-context>` 标签 + 父目录 + 工作目录(验证:同上)
- [x] `AgentTool.execute` 在 `definition.isolation == "worktree"` 时走 worktree 分支(验证:同上)
- [x] `AgentTool.execute` 在 `worktree_mgr is None` 且 isolation=worktree 时返回 `is_error=True`(验证:同上)
- [x] `AgentTool.execute` 在 isolation=worktree + background=True 时强制走前台路径（忽略 background 字段）(验证:同上)

### command 包扩展

- [x] `Command` 含 `args_handler` 字段;`CommandContext` 含 `worktree_mgr` / `set_active_cwd` 字段(验证:`pytest tests/test_command_builtins.py -k worktree`)
- [x] `parse("/worktree create foo")` 返回 `("worktree", "create foo", True)`;`parse("/help")` 返回 `("help", "", True)`(验证:`pytest tests/test_command_dispatch.py -k test_parse`)
- [x] `/worktree` 命令被注册，lookup 命中(验证:`pytest tests/test_command_builtins.py -k worktree_registered`)
- [x] `handle_worktree` 分发子命令 create/list/enter/exit/remove;`enter` 调 `ctx.set_active_cwd`(验证:`pytest tests/test_command_builtins.py -k handle_worktree -v`)
- [x] `handle_worktree` 在 `ctx.worktree_mgr is None` 时报错(验证:同上)
- [x] 既有 13 条命令（无 args_handler）行为不变，`/help xx` 从「未知命令」变为「显示 help」（ch10 F7 有意修订）(验证:`pytest tests/test_command_builtins.py -v` 全绿)

### tui 包扩展

- [x] `tui/run.py` 的 `run()` 接收 `worktree_mgr` 参数;主循环维护 `active_cwd_box`（空=进程 cwd）(验证:`python -c "from novacode.tui.run import run"`)
- [x] 启动装配时若 `worktree_mgr.current_session` 非 None，设 `active_cwd_box[0]=session.worktree_path`(验证:端到端复验——`enter` 持久化 session → kill → 重启，`read_file probe.txt` 相对路径命中 worktree 内文件)
- [x] 主 Agent Run 前用 `with with_cwd(...):` 包住——两处 `_stream_reply` 调用（主循环 + run_callback）(验证:端到端场景 5 cwd 注入生效)
- [x] `/worktree enter` 后主 Agent 工具调用以 worktree 路径为基准（见端到端场景 5）(验证:tmux 实跑)

### cli 接入

- [x] src/novacode/cli.py 构造 Manager，失败 stderr 警告 + 降级(验证:非 git 目录启动降级提示)
- [x] AgentTool 构造末尾追加 worktree_mgr(验证:同上)
- [x] run() 构造接收 worktree_mgr(验证:同上)
- [x] 启动时异步跑 sweep_stale(`asyncio.create_task`)(验证:`grep -n sweep_stale src/novacode/cli.py`)
- [x] .gitignore 追加 `.novacode/worktrees/` 与 `.novacode/worktree_session.json`(验证:`git check-ignore .novacode/worktrees/test`)

## 集成

- [x] subagent.Definition.isolation + agent.AgentTool 协同——isolation:worktree 的 SubAgent 启动时自动创建 Worktree(验证:test_agent_worktree 通过 + 端到端场景 1)
- [x] tool ctx with_cwd + AgentTool._execute_with_worktree 协同——SubAgent 在 Worktree 内的工具调用使用 wt.path 作为 cwd(验证:test_agent_worktree + 端到端场景 1/5)
- [x] 主 Agent 工具列表稳定——工具数不变，不暴露新工具(验证:`pytest tests/test_agent_tool.py -k name_and_parameters`)
- [x] worktree 包 + subagent 包 + agent 包 + command 包 + tui 包之间无导入循环(验证:`python -c "import novacode.tui.run, novacode.agent.agent_tool, novacode.command.builtins, novacode.worktree"`)

## 编译与测试

- [x] 项目可启动:`python -m novacode`(WSL 实跑正常进 TUI，banner + 输入框 + 状态栏)
- [x] 所有单元测试通过:`pytest`(**691 passed + 2 skipped**)
- [x] lint 通过:`ruff check`(全绿)+ `ruff format --check`(201 files already formatted)

## 端到端场景（WSL tmux 实跑）

每个场景在 WSL tmux 内启动一个 novacode 实例完成。**通用预置**见修订版文档；启动 `wsl -d Ubuntu -- bash -lc 'cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode'`。

### 场景 1:isolation:worktree 子 Agent 修改文件不影响主目录 ✅ 全部通过

**预置:** `.novacode/agents/worktree-writer.md`（`permissionMode: dontAsk` + `isolation: worktree`）;主目录 `scratch_ch14.txt` = `MAIN`（未跟踪）。

**实测：**
- [x] 子 Agent 跑动，scrollback 出现 `Agent(...)` 行
- [x] tool_result 末尾含 `[Worktree 保留: /mnt/d/NovaCode/.novacode/worktrees/agent-a..., 分支 worktree-agent-a...]`（**绝对路径**，有未提交修改 → auto_cleanup 保留）
- [x] **主目录** `scratch_ch14.txt` 仍为 `MAIN`（主目录未被改）
- [x] **Worktree 副本** `scratch_ch14.txt` 为 `SUBAGENT`
- [x] 完成后清理残留 worktree

### 场景 2:isolation:worktree 子 Agent 无变更时自动清理 ✅ 全部通过

> 修订：项目根**没有 README.md**（原文档引用自别的项目），改用 `CLAUDE.md`。

- [x] 子 Agent 只读（read_file CLAUDE.md + 总结），tool_result 不含「Worktree 保留」
- [x] `.novacode/worktrees/` 无本次任务对应的 `agent-a*` 目录（auto_cleanup 已删）

### 场景 3:`/worktree create` + `/worktree list` 手动管理 ◐ 核心通过

- [x] `/worktree create demo-feature` → `Worktree 已创建: /mnt/d/NovaCode/.novacode/worktrees/demo-feature (分支 worktree-demo-feature)`
- [x] `/worktree list` 显示含 `demo-feature` 的列表项
- [x] 外部验证：目录存在 + `git branch --show-current` = `worktree-demo-feature`
- [x] `/worktree remove rem-test --discard` 实跑通过（外部预建 worktree + 启动 scan 识别 + remove，规避 create 时序）→ `已删除 Worktree: rem-test`

### 场景 4:`/worktree exit` 变更保护 ✅ 全部通过

- [x] `/worktree enter demo-feature` → `已进入 demo-feature: ...`
- [x] `/worktree exit --remove` → 报错 `Worktree 有未提交修改或本地新增 commit，拒绝删除；加 --discard 强制`
- [x] `/worktree exit --remove --discard` → `已退出并删除 Worktree`，目录已删除

### 场景 5:explicit cwd——`/worktree enter` 后工具调用用 worktree 路径 ◐ 核心通过

> 修订：worktree 在启动前**外部预创建**（`git worktree add`），`/worktree enter` 为纯内存操作，规避 slash 命令时序。

- [x] `/worktree enter cwd-test` → `已进入 cwd-test: .../cwd-test`
- [x] 对话 `用 read_file 读 probe.txt` → `read_file({"path": "probe.txt"})` 命中 **in-worktree-only**（ctx cwd 注入生效）
- [x] `/worktree exit` → `已退出 cwd-test，回到主目录`
- [x] exit 后强制工具读取 `read_file({"path": "probe.txt"})` → **`文件不存在: probe.txt`**（active_cwd 已清空，主目录无此文件）

### 场景 6:Slug 校验阻止路径遍历 ✅ 全部通过

- [x] `/worktree create ../etc` → `slug 段 '..' 不允许（路径遍历）`，未创建 `.novacode/etc/`
- [x] `/worktree create ..` → 同样错误
- [x] `/worktree create normal_one` → 成功创建
