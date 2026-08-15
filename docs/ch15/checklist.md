# Agent Team Checklist

> 本章开发前已按项目实际修订（2026-08-13）。协作工具统一 `Team*` 前缀（避免与 ch13
> 后台元工具重名）；验收以 WSL tmux 实跑为主（iterm2 仅命令构造单测）；
> 主 Agent（Lead）**可见**协作工具（协调队员用），普通 SubAgent 经 `ALL_AGENT_DISALLOWED_TOOLS` 不可见，
> 队员工具集由 Team 装配。
>
> **验收结论（2026-08-13）**：单测 **750 passed + 2 skipped**（ch15 新增 57 个单测，
> 含工具超时闭合 tool_calls 回归、in-process 续派、文件锁超时、误调用兜底、disk 写失败、
> tmux split 失败）、`ruff check` / `ruff format --check` 全绿、**`mypy src/novacode/team` 全绿**。WSL tmux 端到端实跑（真实 deepseek）四个场景全部通过：
> ① **tmux 后端 Team 全生命周期**（TeamCreate → `Agent(team_name)` spawn bob split pane →
> worktree → 任务执行 → TeamSendMessage 通知 → Lead 自动唤醒 → 续派 → `/team` 命令 → delete --force）；
> ② **in-process 后端**（临时移走 tmux 二进制让 `detect` 走 in-process：同进程 asyncio task、
> 无 split pane、agent_id=`task_xxx`、is_active=false、idle 通知、Lead 自动唤醒）；
> ③ **Coordinator Mode**（状态栏 `[COORDINATOR]`、write_file 被剥夺/LLM 用 bash 转写、bash 可用、派队员）；
> ④ **Plan 审批**（planner plan 模式起步发 Plan → Lead 发 `plan_approval_response(approve:true)` →
> planner 切 default 加回写工具执行，worktree 产出 hello_world.py）。
> **修复真实 bug**：agent 工具执行超时（`_run_one` 的 `wait_for` 30s）直接抛 TimeoutError 导致
> assistant tool_calls 无 tool_result 回灌 → conv 孤儿 → 后续请求 400；已改 `_run_one` 超时转
> 结构化错误 + `_safe_task_result` 兜底（工具异常不崩批处理），保证 tool_calls 闭合。

## 实现完整性

- [x] `team.Manager` 可被实例化:`team.Manager(Path.home(), root, wt_mgr, task_mgr, name_reg)` 返回非 None（验证:`pytest tests/test_team_manager.py -q` 通过）
- [x] `await team.Manager.create("demo", "", "")` 在 `<home_dir>/.novacode/teams/demo/config.json` 落地（验证:`test_manager_create_sanitize_suffix`，检查文件存在）
- [x] `await team.Manager.create("foo bar/baz", "", "")` sanitize 后路径为 `~/.novacode/teams/foo-bar-baz/`（验证:单测 `test_sanitize` / `test_manager_create_sanitize_suffix`）
- [x] 同名 Team 第二次 create 自动后缀 `-2`（验证:`test_manager_create_sanitize_suffix` 断言 `foo-bar-baz-2`）
- [x] `team.BackendType` 三个值齐全:`TMUX` / `ITERM2` / `IN_PROCESS`（验证:枚举定义 + `test_team_backend.py`）
- [x] `backend.detect()` 在 `$TMUX` 设置时返回 `TMUX`;两环境变量都清空时返回 `IN_PROCESS`（验证:`test_detect_tmux_env` / `test_detect_in_process` / `test_detect_iterm2`）
- [x] `mailbox.Box.write` + `mailbox.Box.read` 一进一出消息字段一致（验证:`test_box_write_read_mark`）
- [x] `mailbox` 文件锁在 stale 10 秒后能被新 writer 抢占（验证:`test_acquire_stale`，11 秒前锁）
- [x] `registry.AgentNameRegistry.register("alice", "agent-xxx")` 后 `resolve("alice")` 返回 `"agent-xxx"`,`name_of("agent-xxx")` 返回 `"alice"`（验证:`test_team_registry.py`）
- [x] `tasks.Store.create` 返回的 task id 形如 `task_<6 位 hex>`（验证:`test_create_id_format`）
- [x] `await tasks.Store.update(id_, Patch(add_blocked_by=[other]))` 同时给 other 任务的 `blocks` 加上 id（验证:`test_dependency_bidirectional` 断言双向）
- [x] `await tasks.Store.list_(Filter(status=PENDING))` 返回结果带 `is_ready` 字段，反映 blocked_by 是否全 completed（验证:`test_is_ready`）
- [x] `coordinator.is_enabled` 在 feature flag 关 + 环境变量开时返回 False（验证:`test_double_lock` 4 种组合，只有 11 为 True）
- [x] `coordinator.allowed_tools()` 含 `bash` 不含 `write_file` / `edit_file`，含全部 5 个 `TeamTask*`/`TeamSendMessage`（验证:`test_allowed_tools`）
- [x] 主 Agent（Lead）工具定义含 `TeamTaskCreate` / `TeamSendMessage` 等（协调队员用）;普通 SubAgent 经 `apply_agent_tool_filter` 不含（验证:`test_constants`（ALL 并入 TEAMMATE_TOOLS）+ 端到端 Lead 实际调用 TeamSendMessage 成功）
- [x] `tool.apply_agent_tool_filter` 基础结果不含 5 个协作工具（`ALL_AGENT_DISALLOWED_TOOLS` 已并入 `TEAMMATE_TOOLS`）;`filter(基础)+TEAMMATE_TOOLS` 含全部（验证:`test_tool_filter.py::test_constants` + 队员 spawn 装配）
- [x] 7 个新工具注册到 registry 后，`registry.definitions()` 输出含 `TeamCreate` / `TeamDelete` / `TeamTaskCreate` / `TeamTaskGet` / `TeamTaskList` / `TeamTaskUpdate` / `TeamSendMessage`（验证:cli wire 注册 + 端到端四场景实际调用各工具成功）
- [x] `Team.add_member` 与 `Team.set_member_active` 调用前先 `reload_from_disk_locked` 重读 disk（验证:`test_reload_from_disk_locked`——模拟子进程先写 alice 到 disk，`set_member_active` 走 reload 成功）

## 集成

- [x] `Agent` 工具不带 `team_name` 时走 ch13 原路径，行为不变（验证:`pytest tests/test_agent_tool.py` 全过）
- [x] `Agent` 工具带 `team_name="demo"` 时调 `team_hook.spawn_teammate`（验证:`test_agent_tool_team_branch` mock team_hook 断言被调用）
- [x] `spawn_teammate` 创建 worktree 路径为 `.novacode/worktrees/team-demo+alice`（验证:`test_spawn_inprocess_member` 断言 `team-inproc+bob` 目录 + 端到端）
- [x] `spawn_teammate` 后 `team.members` 含 alice，持久化到 `config.json`（验证:`test_spawn_inprocess_member` 读回 config + 端到端 config.json）
- [x] in-process 后端的队员 ctx 含 TeammateContext，其 backend_type=in-process;该队员调用 `Agent(team_name=...)` 被拒绝并抛 `InProcessTeammateNoSpawnError`（验证:`test_agent_tool_team_blocked_inprocess`）
- [x] 队员 `Agent.run` 每轮通过 `pre_iteration_reminders` 读取 mailbox 未读消息，以 `<incoming-messages>` reminder 注入到 LLM 输入（验证:`test_ingest_builds_reminder` 捕获闭包返回 + `test_build_incoming_reminder_format`）
- [x] 队员收到 `plan_approval_response(approve=True)` 后 `Agent._permission_mode` 切换到 `Mode.DEFAULT`、`_allowed_tools` 加回写工具（验证:`test_ingest_plan_approve_switches` + 端到端场景 4 planner 切 default 写出 hello_world.py）
- [x] `plan_mode_required` 队员 spawn 后初始 `_permission_mode=Mode.PLAN`、工具集为只读基础 + Team 协作工具（不含 write_file/edit_file/bash）（验证:`test_ingest_plan_reject_keeps_plan` + 端到端场景 4 config `plan_mode_required: true`）
- [x] 队员 `run_to_completion` 结束触发 `on_task_done` 回调，Team config 中该成员 `is_active=False`（验证:端到端 in-process/tmux bob 完成 → `is_active: false`）
- [x] 队员 idle 后 Lead mailbox 收到 `summary="<name> idle"` 消息（验证:三个后端端到端 lead.json 均含 idle 消息）
- [x] `TeamSendMessage(to="alice", ...)` 在 alice 已 stop 且为 in-process 后端时，通过 `task_mgr.send_message` 续派（验证:`test_send_message_resumes_inprocess` 断言 sent + is_active 回 True）;Pane 后端时通过 `backend.wake` 让子进程读 mailbox 自然续派（验证:端到端 tmux step2 追加）
- [x] 所有 Team 队员一律 `dont_ask=True`（覆盖角色 frontmatter 的 `permission_mode`），子进程没人能应答 ApprovalRequest 不会卡死（验证:端到端三后端 bob/planner 任务正常完成无卡 Ask）
- [x] Pane 后端 spawn 时 `initial_prompt` 通过预写入 mailbox(type=text, from=lead) 送达，子进程不需要走 CLI 参数（验证:端到端 tmux spawn 后检查 bob mailbox 有 from=lead 初始任务）
- [x] Pane 后端子进程命令行含 `--agent-id <id>` 参数（验证:`test_build_member_cmd` 断言 `--agent-id agent-abc`）
- [x] Pane 后端的 `python -m novacode --team-member` 子进程**不启动 TUI**（不构造输入框、不打印 banner），跑 `run_team_member` 自治协程（验证:端到端 tmux 新 pane 显示 `● tool(args)` 日志流而非输入框）
- [x] Lead mailbox watcher（`consume_lead_mail`）每秒轮询所有 Team 的 lead.json，把未读消息转 `<team-update>` reminder 推 `pending_reminders` + 给 `lead_mail_event` `set()`（验证:端到端 Lead 自动出现处理汇报，lead.json `read: true`）
- [x] Lead 在等待输入时收到 `lead_mail_event`，输入框退出 → `_autonomous_turn()` 合成 user 消息自动开新轮（验证:端到端四场景 Lead 均无需输入自动出现 `[team-update]`/处理汇报）
- [x] `/team list` 输出含 `<home_dir>/.novacode/teams/` 下所有 Team（验证:端到端 `/team list` 输出 `inproc tmux 2 成员 [2/2] 活跃`）
- [x] `/team delete demo --force` 调 `backend.kill` 杀 pane（tmux/iterm2）+ 清 worktree + 清 team 目录（验证:端到端 delete --force 后 teams/ 与 worktrees/ 均清空）
- [x] 沙箱开放 `/tmp` 与 `/private/tmp`（macOS 真实路径）作为白名单——write_file/edit_file 可写 `/tmp/foo.txt`，但 `/etc/passwd` 仍拒（验证:`tests/test_sandbox.py::test_tmp_whitelist` / `test_root_based`）

## 编译与测试

- [x] `python -m novacode --help` 能正常启动且打印帮助（验证:命令退出码 0，usage 含全部 --team-member flags）
- [x] `ruff check src/` 无警告（验证:`ruff check src/novacode tests` All checks passed）
- [x] `ruff format --check src/` 无未格式化文件（验证:242 files already formatted）
- [x] `pytest` 全部通过（验证:747 passed + 2 skipped）
- [x] `mypy src/novacode/team/` 全绿（验证:`mypy src/novacode/team src/novacode/coordinator src/novacode/agent/team_hook.py src/novacode/agent/team_mailbox.py` → `Success: no issues found in 30 source files`；修了 `callable`→`Callable` 注解、member.py `msgs` 变量名冲突、`Message` 缺 `content`、manager `wt_mgr/registry` 改 `Any`）

## 端到端场景（WSL tmux 实跑）

> 本章核心验收。四场景均已在 WSL tmux 会话内真实 deepseek 实跑。预置:`D:\NovaCode` 已是
> 本地 git 仓库（ch14 git init）；e2e 期间临时用 `.novacode/settings.local.yaml` 放行
> Agent/Bash/Team 工具（测后已删——default 模式下调这些工具会弹审批，手动测试按 `1` 或重建放行）。

**场景 1:tmux 后端，Team 全生命周期**（实跑用 inproc/bob 等价命名）✅ 全部通过

- [x] `tmux new-session -s ch15-test` 进入新 tmux 会话（实跑 `ch15` 会话）
- [x] `cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode` 启动 TUI;启动消息正常，无 ch15 相关 error
- [x] 在 TUI 输入:「创建一个名为 demo 的团队」→ `TeamCreate` 返回 `{"team_name":"inproc","backend":"tmux","config_path":"/root/.novacode/teams/inproc/config.json"}`
  - [x] 验证:`config.json` 存在，`backend` 字段为 `tmux`
- [x] 在 TUI 输入:「派 alice 用 general-purpose 角色，在 worktree 里跑 `echo hello > /tmp/test_alice.txt && pwd > ...`」→ `Agent(team_name=..., name="bob", ...)`
  - [x] 验证 a:tmux 自动 split 出右侧 pane（`%1 python` = team-member 子进程）
  - [x] 验证 b:新 pane 内显示自治循环日志流（`● bash(...)` / `● TeamSendMessage(...)`，非输入框）
  - [x] 验证 c:`.novacode/worktrees/team-inproc+bob/` 目录存在
  - [x] 验证 d:`cat /tmp/bob.txt` 内容为 `step1`
  - [x] 验证 e:命令在 worktree cwd 执行（bash 工具 cwd=resolve_path，ch14 已验证 cwd 注入）
  - [x] 验证 f:`config.json` 中 `members` 含 bob，`backend_type="tmux"`，`pane_id="%1"` 非空
  - [x] 验证 g:bob mailbox 含一条 `from=lead` 的 text 消息（initial_prompt 预写入证据）
- [x] 在 TUI 输入 `/team info inproc` → 输出含 bob 行（agent_id/worktree/active）
- [x] 在 TUI 输入:「给 alice 发消息，让她再写一行 world」→ `TeamSendMessage(to="bob", ...)`
  - [x] 验证 a:bob pane 被唤醒（send-keys 触发，pane 显示新执行流）
  - [x] 验证 b:`cat /tmp/bob.txt` 看到第二行 `step2`（追加成功，两行）
- [x] 等待 bob 任务自然结束
  - [x] 验证 a:`config.json` 中 bob `is_active: false`（跨进程 reload 反映到 disk）
  - [x] 验证 b:lead mailbox 含 `summary` 含 `idle` 的消息，`read: true`（watcher 已消费）
  - [x] 验证 c:Lead 屏幕无需用户输入自动出现处理 bob 汇报的回复（自动唤醒）
- [x] 在 TUI 输入 `/team delete inproc --force` → `团队 inproc 已删除`
  - [x] 验证 a:`ls ~/.novacode/teams/` 无 inproc 目录
  - [x] 验证 b:`.novacode/worktrees/` 无 `team-inproc+bob`
  - [x] 验证 c:`tmux list-panes` 只剩 Lead pane

**场景 2:in-process 后端实跑** ✅ 核心通过（续派/delete 由单测 + 场景 1 覆盖）

- [x] 无 tmux 环境启动（实跑用 `mv /usr/bin/tmux /usr/bin/tmux.bak` + `env -u TMUX` 让 detect 走 in-process，客户端用 tmux.bak 交互）
- [x] 在 TUI 输入:「创建 inproc 团队」→ `config.json` `backend` 为 `in-process`
- [x] 在 TUI 输入:「派 bob 用 general-purpose，在 worktree 里 `echo step1 > /tmp/bob.txt`」
  - [x] 验证:无新终端窗口/pane 出现（`list-panes` 仅 `%0`，同进程 asyncio task）
  - [x] 验证:`/tmp/bob.txt` 内容 `step1`；bob `agent_id="task_0fb16678"`（in-process 特征）
- [x] 等 bob 结束:`config.json` `is_active=false`;Lead 自动出现处理 bob 汇报（自动唤醒）
- [x] 续派 step2（in-process `task_mgr.send_message` 路径）——**e2e 实跑通过**（`TeamSendMessage` → bob 追加 step2，`/tmp/bob.txt` 两行，bob 汇报、Lead 自动确认）+ 单测 `test_send_message_resumes_inprocess`
- [x] `/team delete inproc --force`——**e2e 实跑通过**（输出「团队 inproc 已删除」，teams/ 与 worktrees/ 均清空）

**场景 3:Coordinator Mode 实跑** ✅ 全部通过

- [x] `.novacode/config.yaml` 加 `features:\n  coordinator_mode: true`（snake_case，测后已恢复原样）
- [x] `novaCODE_COORDINATOR_MODE=1 python -m novacode`
- [x] 观察 TUI 状态栏出现 `[COORDINATOR]` 标签（`DEFAULT [COORDINATOR] · deepseek-v4-flash`）
- [x] 在 TUI 输入:「写一个 hello world 到 /tmp/coord_test.txt」→ `write_file` 不在 Lead 工具集，LLM 用 `bash echo` 转写成功（`/tmp/coord_test.txt` = `hello world`）
- [x] 在 TUI 输入:「跑 `git status`」→ Agent 调 `bash` 正常执行（bash 在 Coordinator 白名单）
- [x] 在 TUI 输入:「派几个队员探索 src/novacode/agent 和 src/novacode/team」→ Lead 派队员 reader1 读源码并总结；**未立刻自己 read_file/glob/bash 探索**（纪律段约束）；TeamDelete 清理

**场景 4:Plan 审批工作流** ✅ 全部通过

- [x] 准备角色定义 `.novacode/agents/planner.md`，frontmatter 含 `plan_mode_required: true`（测后已删）
- [x] 启动 novacode，创建 team `plan-test`
- [x] 在 TUI 输入:「派 planner 用 planner 角色，在 worktree 制定 hello world 程序的实现计划」→ planner 以 plan 模式起步（config `plan_mode_required: true`，工具集只读+Team），生成完整计划（目标/验收/步骤/风险）后 `TeamSendMessage` 发给 lead
  - [x] 验证:lead mailbox 含计划消息
- [x] 批准 → Lead 发 `TeamSendMessage(to="planner", type="plan_approval_response", payload={"approve":true})`
  - [x] 验证:planner mailbox 收到 `plan_approval_response`，切 `set_permission_mode(DEFAULT)` + 写工具加回，执行写出 `hello_world.py`（中文 docstring + main + print）

## 失败回归

- [x] novacode 启动时 `<home_dir>/.novacode/teams/` 不存在，自动创建，不报错（验证:`Manager.__post_init__` mkdir + `test_manager_create_*`）
- [x] `<home_dir>/.novacode/teams/<somename>/config.json` 内容损坏时，启动只 stderr 警告，跳过该 Team（验证:`test_broken_config_skipped`）
- [x] 创建 Team 时若 disk 写失败，抛错，内存 teams 不留半成品（验证:`test_manager_create_disk_fail`——monkeypatch `atomic_write_json` 抛 OSError，`Manager.create` 抛且 `get() is None` / `list_() == []`）
- [x] mailbox 文件锁抢占冲突 10 次仍失败时，TeamSendMessage 抛错，不丢消息（验证:`test_acquire_timeout`——非 stale 锁持 10 次后抛 TimeoutError）
- [x] tmux 后端在 `tmux split-window` 失败时，抛错不静默回退 in-process（验证:`test_tmux_spawn_failure`——mock `create_subprocess_exec` 返回 returncode=1，`TmuxBackend.spawn` 抛 `RuntimeError("tmux spawn 失败...")`）
- [x] 协作工具在无活跃 Team 时被调用，工具自己抛错兜底（验证:`test_send_message_no_team`——`resolve_team` 抛「当前没有活跃 Team」，工具转 is_error）
