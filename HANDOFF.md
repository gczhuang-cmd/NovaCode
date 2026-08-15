# NovaCode 交接文档

> 给完全没有上下文的后续对话。读完本文档即可接手 NovaCode 的开发。

## 1. 项目概览

**NovaCode** 是一个终端 AI 编程助手（类似 Claude Code），用 Python 3.12 实现，位于 `D:\NovaCode`。

**架构核心（务必先理解）**：
- **TUI 是 prompt_toolkit 非全屏 + Rich `console.print` 直写终端 scrollback**。不是 Textual、没有 `App` 类、没有虚拟屏幕。
- 输出（对话/工具行/命令结果）直接打进程终端 scrollback；输入框是每轮临时创建的 prompt_toolkit Application（`tui/run.py` 的 `_input_frame`）。
- 命令 handler 通过 **`CommandContext`**（`src/novacode/command/context.py`，dataclass）访问能力——这是唯一的抽象层，**不要再引入 UI Protocol / Textual App**（ch10 修订时已废弃该设想）。
- 主循环在 `tui/run.py` 的 `run()`：`_input_frame` 收输入 → `dispatch_slash` 分发命令 / 走 `_stream_reply` 调 Agent。
- 启动装配在 `cli.py`：配置 → 工具注册 → 命令注册 → Catalog（skill）→ `run()`。

**目录结构**：
```
src/novacode/
├── cli.py             # 启动装配
├── agent/             # ReAct 循环（Agent.run 是 async generator 吐 Event）、SessionRuntime
├── command/           # slash 命令系统（command.py/registry.py/dispatch.py/context.py/builtin_*.py/skills.py）
├── compact/           # 上下文压缩（layer1/layer2/state/token）
├── config.py          # 配置加载 + effective_context_window
├── conversation.py    # 对话历史（on_append/on_replace 回调 → JSONL 落盘）
├── instructions/      # novaCODE.md 指令加载
├── llm/               # Provider 抽象（anthropic/openai）+ new_provider
├── mcp/               # MCP 工具适配
├── memory/            # 自动笔记记忆（Store/Manager）
├── permission/        # 权限引擎（Mode/Outcome/Engine）
├── prompt/            # 系统提示工程（modules.py/skills_block.py/reminder.py）
├── session/           # JSONL 会话存档（Writer/list/load/cleanup）
├── skills/            # Skill 系统（types/parser/catalog/active/render/adapter/executor/install/builtin）
├── tool/              # 工具（Registry/bash/read_file/.../load_skill/install_skill/skill_tool）
└── tui/               # run.py（主循环）/resume.py/view.py/completion.py/select.py
```

## 2. 当前进度

已完成 **ch09（记忆+会话持久化）、ch10（slash 命令）、ch11（Skill 技能包）、ch12（Hook 生命周期挂钩系统）、ch13（SubAgent 机制）、UX01（流式期间实时反馈）、ch14（Worktree 隔离）、ch15（Agent Team 多人协作）**。功能章节文档在 `docs/ch0X/{spec,plan,task,checklist}.md`；**UX01 是独立体验优化**，文档在 `docs/ux01/`。

当前测试状态：**750 passed + 2 skipped、ruff/格式全绿**（本机 `.venv`，`pytest -q`；**`mypy src/novacode/team` 全绿**；mypy 对 ch14 新增模块干净，既有代码仍有存量错误）。**ch15 WSL tmux 端到端四个场景全过**（真实 deepseek）：tmux 后端 Team 全生命周期、in-process 后端（临时移 tmux 二进制）、Coordinator Mode（`[COORDINATOR]` 标签 + write_file 剥夺）、Plan 审批（plan 模式起步 → plan_approval_response → 切 default 执行），详见 §3 ch15 段落与 docs/ch15/checklist.md。**ch14 tmux 端到端已实跑**（WSL tmux 真实 deepseek）：场景 1/2/4/6 全部通过、场景 3/5 核心路径通过（详见 §3 ch14 段落与 docs/ch14/checklist.md）。**ch13 tmux 端到端 13/13 场景通过**。场景 10（超时切后台）初测发现 adopt 后「卡 running + result 空」，排查确认是 `asyncio.wait_for` 超时 **cancel** 子 Agent 协程所致——已改 `asyncio.wait` 不 cancel + adopt 接管同一 run_task，复验后台正常跑完、result 正常、主 Agent 可感知（见 §6.6 坑 30）。

### UX01 流式期间实时反馈（独立于 chXX）
- **思考内容展示**：openai 协议捕获 `delta.reasoning_content`（实测 deepseek-v4-flash 输出约 62% 是思考 token）→ `StreamEvent.thinking` → `Event.thinking` → TUI Live 区实时滚动（最近 3 行），Ctrl+O 展开全文/再按折叠。思考**只经 TUI 层，不进 conversation/JSONL/回灌**（推翻 ch02 AC5「思考增量接收即丢弃」）。
- **工具参数进度**：`delta.tool_calls[].arguments` 增量转发为 `StreamEvent.tool_delta` → Live 区显示「⚙ 正在生成 write_file 参数…(已 N 字符)」实时增长；完整 ToolCall 拼接/执行路径不变。
- **完成统计**：回复完成「· 用时 Xs · 思考 N tokens」（真实 `usage.reasoning_tokens`，tokens 全称，不重复 in/out——状态栏负责累计）。
- 新增 `tui/thinking.py`（`_ThinkingView` 双缓冲 deque+list、`_ToolProgress`）；`_CancelWatcher` 扩展 Ctrl+O（0x0f）；`_Phase` 状态机（thinking/generating/text/tools/idle）驱动 Live 渲染。
- **端到端已验证**：WSL tmux 真实 deepseek 全部 7 场景通过。

## 3. 已实现的关键功能

### ch10 slash 命令
- `command` 包：`Kind`（LOCAL/UI/PROMPT）、`Command`（含 `is_skill` 字段）、`Registry`（register/冲突检测/lookup/prefix_match/remove_if）、`parse()`（大小写不敏感、零参数）、`CommandContext`。
- 12 条内置命令：clear/compact/do/exit/help/memory/permission/plan/resume/session/skill/status（**`/review` 已从内置删除**，由 Skill 接管）。
- 自动补全：prompt_toolkit 原生 `CommandCompleter` + `CompletionsMenu`（`tui/completion.py`）。
- banner 换成 pyfiglet 生成的 ansi_shadow 风格实心 NOVACODE（`prompt/__init__.py`）。

### ch11 Skill 技能包
- `skills` 包：SKILL.md 解析（frontmatter）、Catalog 三层扫描（内置<用户<项目）、ActiveSkills 激活列表、render（$ARGUMENTS）、Executor（inline/fork）、install（zip-slip 防护）、内置 commit/review/test 三个 Skill。
- 工具：`LoadSkill`（系统工具，激活 SOP 到 env context）、`InstallSkill`（普通工具，zip 安装）、`Tool.is_system` 标记、`definitions_filtered`、`register_skill_tool`、`skill_tool`（subprocess exec）。
- 两阶段加载：system prompt 第一层 `skills-catalog`（名字+描述）；env context 第二层 `active-skills`（激活的 SOP）。
- `prompt/skills_block.py`：`render_skills_catalog` / `render_active_skills_block`。
- fork 子 Agent：独立 SessionRuntime（继承主 context_window）、Mode.BYPASS（无 UI 响应 approval）、FORK_TIMEOUT=180s 超时、usage 回写主会话。

### ch12 Hook 生命周期挂钩系统
- `hook` 包：`event.py`（11 个 Event + `is_blocking`/`parse_event`）、`rule.py`（Rule/Condition/Action/Payload）、`matcher.py`（`eval_condition`/`get_by_path`）、`loader.py`（两层 YAML 扫描 + 字段校验 + 跨文件同名冲突）、`engine.py`（`Engine.dispatch` + only_once + `reset_for_new_session`）、`executor.py`（四类动作：shell exit2 拦截 / prompt / http decision=block / subagent stub）。
- `permission/matcher.py` 新增：`Matcher` Protocol + `Exact/Glob/Regex/Not` 四种 + `compile_matcher`（`=`精确 `~`正则 `!`反向 无前缀 glob）；`Rule.pattern` 改 `matcher`；`_RULE_RE` 放宽支持模式段内括号（正则 `(a|b)`）；`to_rule_set` 失败 rule 打 stderr。
- 事件挂接：SessionStart（`tui/run.py` 主循环前 + `/clear` handler）、UserPromptSubmit（`tui/run.py` 主循环拦截，非全屏提示后 continue）、PreToolUse/PostToolUse/PreCompact/PostCompact/PreUserMessage/Stop/Notification（`agent/__init__.py`，PreUserMessage 注入的 prompt 下一轮进 reminder）、SessionEnd（cli 退出兜底统一 emit）。
- `/hooks` 命令（`command/builtin_hooks.py`，Kind.LOCAL）：按 event 分组 + `Loaded from:`；`[once]`/`[async]` 标志需 `\[` 转义（rich 会吞样式标签）。
- 关键实现细节：agent 模块自身有 `Event` dataclass，hook 枚举 import 用 `Event as HookEvent`；`SessionRuntime.reset_for_new_session` 是同步方法，hook 引擎 only_once 重置由 `/clear`/`/resume` handler 另调 `engine.reset_for_new_session()`；hook 的 http 动作 `trust_env=False`（测试环境代理拦截本地请求）；Windows 下 shell 动作走 cmd.exe、输出 GBK 解码。

### ch13 SubAgent 机制
- `subagent` 包：`Definition`/`Source`（内置<用户<项目）、`parser.py`（frontmatter 解析 + 非法 model/permissionMode 降级 + stderr 警告）、`catalog.py`（三层加载、同名高优先级覆盖、`fork_definition()` 返回 `__fork__`）、`embed.py`（importlib.resources 读内置）、`builtin/{general-purpose,Explore,Plan}.md`。
- `task` 包：`Manager`（`launch`/`adopt_running`/`stop`/`send_message`/`subscribe_done`/`upgrade_approval`）、`BackgroundTask`/`PartialState`/`Status`、4 个元工具（TaskList/TaskGet/TaskStop/SendMessage，`is_system=False`）。
- `agent` 扩展：5 个新构造参数（`system_prompt`/`max_turns`/`permission_mode`/`dont_ask`/`approval_upgrader`）；`run_to_completion`（`async for` 复用 `run`，转发 tool/usage 事件）；`fork.py`（`build_forked_messages`/`is_fork_context`/`FORK_BOILERPLATE`）；`agent_tool.py`（AgentTool + `AUTO_BACKGROUND_SECONDS=120`，前台超时自动切后台）；`launch.py`（`launch_fork`，Skill fork 与 Fork 路径共用）；`permission_upgrade.py`（`ApprovalUpgrader` + `default_upgrader` 默认拒绝）。
- `tool/filter.py`：`ALL_AGENT_DISALLOWED_TOOLS = ["Agent","TaskList","TaskGet","TaskStop","SendMessage"]`、`ASYNC_AGENT_ALLOWED_TOOLS`（后台白名单，mcp__ 前缀动态放行）、`apply_agent_tool_filter`（spec F30 五层顺序）。
- 挂接：cli 装配（`subagent.load_catalog`/`task.Manager`/5 工具注册，Agent 工具 `parent=None` 由 `tui.run` 构造主 Agent 后 `set_parent`）；`tui/run.py` 加 `task_mgr`/`subagent_catalog` 参数 + 每轮 `_inject_task_notifications`；`tui/tasks.py`（`build_task_notification` 注入 `pending_reminders`，复用 ch12 通道）。
- **关键设计决策（将来扩展前必读）**：
  1. **`model` 字段本期不切 provider**（单 provider 环境，恒用父）。将来做多模型，改 3 处即可：`config.py` 加「按 model 查找 ProviderConfig」的 `resolve()`（~10 行）、`agent_tool.py` execute 里一个 if 分支 + **context_window 必须跟随 `effective_context_window(resolved_cfg)`**（否则不同上下文窗口模型会频繁压缩，ch11 坑 11 同理）、统一 skill fork 的 provider 选择。**不要用 ch11 的 `dataclasses.replace(cfg, model=...)` 做法**——那是同 provider 换型号；跨 provider（不同 base_url/api_key）必须按 model 查配置，否则拿错 api_key。
  2. **SubAgent 与未来 Agent Team 的隔离边界**：`ALL_AGENT_DISALLOWED_TOOLS` 只约束 **SubAgent 过滤路径**（AgentTool/`launch_fork` 构造子 Agent）。Agent Team 章节的队员协调工具（TaskCreate/TaskGet/TaskList/TaskUpdate/SendMessage）是「**注册进去的，不是过滤留下的**」——将来 Team 直接往队员 registry 注册，不经过 `apply_agent_tool_filter`，不受本列表约束；同名协调工具覆盖个人版即可。改 Team 时**不要**复用 `ALL_AGENT_DISALLOWED_TOOLS` 去过滤队员工具集。
  3. **子 Agent Ask 不弹主 TUI 窗**：`approval_upgrader` 默认实现 `default_upgrader` 返回「拒绝本次」回灌（根因：Tool 协议无 ToolContext + 前台 inline 子 Agent 阻塞在主 Agent 工具执行内，事件流暂停无法弹窗）。`dontAsk` 兜底（`dont_ask` 字段短路 Ask→Allow）仍完整实现。
  4. **嵌套阻断收敛为工具过滤**：Fork 与定义式子 Agent 的工具列表都不含 Agent 工具（`ALL_AGENT_DISALLOWED_TOOLS` 统一剔除）；`is_fork_context` 只作辅助/测试函数，不作运行时拦截闸。

### ch14 Worktree 隔离
- `worktree` 包：`slug.py`（validate_slug/flat_slug）、`session.py`（WorktreeSession + JSON 原子持久化）、`git.py`（`_run_git`/`_has_worktree_changes`/`_resolve_head_sha_from_fs`）、`manager.py`（Worktree + Manager，mixin 组合 `CreateMixin`/`LifecycleMixin`/`SweepMixin`）、`create.py`（create + 快速恢复 + post-creation setup A/B/C/D）、`lifecycle.py`（enter/exit/remove/auto_cleanup）、`sweep.py`（sweep_stale + random_agent_name）。
- `tool/ctx.py`：ContextVar 显式 cwd（`with_cwd`/`cwd_from_ctx`/`resolve_path`），6 个核心工具（read_file/write_file/edit_file/**glob_tool**/grep_tool/bash）用 resolve_path；bash 子进程 `cwd=resolve_path("")`。Tool schema 不变。
- `subagent.Definition.isolation`（"" / "worktree"），parser 解析 `isolation:` frontmatter（**小写键**，非法回落空）。
- `agent/agent_worktree.py`：`_execute_with_worktree`（`create("HEAD", manual=False)` → notice 前缀 → `with_cwd(wt.path)` 包 `run_to_completion` → `auto_cleanup`），`AgentTool` 加 `worktree_mgr` 字段，isolation=worktree 时**强制前台**（F23）。保留文案 `[Worktree 保留: <path>, 分支 <branch>]`。
- `/worktree` 命令（Kind.UI，`args_handler`）：create/list/enter/exit/remove。ch10 F7「零参数」修订为 `parse` 返回三元组 (name, tail, is_slash)，`Command.args_handler` 带参处理，`/help xx` 从「未知命令」变「显示 help」（同步更新 test_parse）。
- `tui/run.py`：run() 加 worktree_mgr 参数、内部 `active_cwd_box`（空=进程 cwd），两处 `_stream_reply`（主循环 + run_callback）前 `with_cwd(_effective_cwd())` 包住；CommandContext 加 `worktree_mgr`/`set_active_cwd` 字段。
- `cli.py`：`Manager(root)` 失败降级 None（stderr 警告），`asyncio.create_task` 后台跑 `sweep_stale(24h)`。
- **关键实现细节**：`Manager.current_session` 是**实例属性**（不是方法）；变更检查 `_has_worktree_changes`/sweep **忽略 `.novacode/` 与软链大目录**（`.venv`/`node_modules`/`vendor`——post-creation setup 复制/软链的条目，否则只读子 Agent 也被判定有变更）；`_resolve_head_sha_from_fs` 读 `.git` **文件**（`gitdir: <path>` 行）→ HEAD → ref（common dir 回退）；Windows 下 `subprocess.run` 必须 `encoding="utf-8"`（git 输出 UTF-8，GBK 解码失败时 stdout=None 导致 AttributeError）。

### ch15 Agent Team
- `team` 顶层包：`types.py`（Team/TeammateInfo/BackendType/异常类）、`persistence.py`（sanitize/原子写/reload_from_disk_locked）、`manager.py`（create/get/delete/handle_task_done/poll_lead_mailboxes + active_team）、`spawn.py`（spawn_teammate 主流程）、`member.py`（run_team_member 自治循环）、`filelock.py`（跨进程文件锁）、`mailbox/`（Box+Message）、`registry/`（AgentNameRegistry，task.Manager 委托）、`tasks/`（Store+Filter/Patch+is_ready 双向依赖）、`backend/`（Backend Protocol + detect + tmux/iterm2/inprocess）、`tools/`（TeamCreate/Delete + TeamTask\* + TeamSendMessage）。
- **协作工具命名（关键决策）**：ch13 已注册后台元工具 TaskList/TaskGet/TaskStop/SendMessage，ch15 协作工具**加 Team 前缀**（TeamTaskCreate/TeamTaskGet/TeamTaskList/TeamTaskUpdate/TeamSendMessage）避免 `tool.Registry` 重名崩溃。**主 Agent（Lead）可见协作工具**（协调队员、Plan 审批用）；普通 SubAgent 经 `ALL_AGENT_DISALLOWED_TOOLS`（并入 TEAMMATE_TOOLS）不可见；队员工具集由 Team 装配（`apply_agent_tool_filter(基础)+TEAMMATE_TOOLS`，HANDOFF ch13 设计决策 2「注册进去，不是过滤留下的」）。
- `agent` 扩展：`TeamHook` Protocol（team_hook.py）、`TeamSpawnRequest`/`TeammateContext`（ContextVar，参照 ch14 `with_cwd`）、`ingest_team_mailbox`（pre_iteration_reminders 闭包 + Plan 审批切模式）、`Agent` 新增 `exclude_tools`/`pre_iteration_reminders`/`system_suffix` 参数 + `set_permission_mode`/`set_allowed_tools` 方法；`AgentTool` 加 `team_name` 参数 + `team_hook` 委托。
- `task.Manager`：加 `name_reg`（AgentNameRegistry 委托）+ `on_task_done` 回调（launch/send_message/adopt 的 runner finally 触发）+ `set_name_registry`。
- `coordinator` 包：双锁 `cfg.features.coordinator_mode and env_truthy(novaCODE_COORDINATOR_MODE)`，`COORDINATOR_ALLOWED_TOOLS`（含 Team\* + read_file/glob/grep/bash，剥夺写工具），`SYSTEM_PROMPT_SUFFIX`（派完队员就停手纪律）。
- `config.py`：加 `FeaturesConfig`（coordinator_mode/fork_teammate，snake_case）。
- TUI：`run()` 加 team_mgr/coordinator 参数；`tui/leadmail.py::consume_lead_mail` 每秒轮询所有 Team 的 Lead mailbox → `<team-update>` reminder + event；输入框收到信号 `app.exit(TEAM_AUTONOMOUS)` → 主循环自动回合（合成 user 消息开 Run，用户可见）；状态栏 `[COORDINATOR]` 标签。
- `cli.py`：argparse `--team-member` 等 flags；wire `AgentNameRegistry` → `team.Manager` → 7 新工具 + on_task_done；`--team-member` 分支跑 `run_team_member`（不构造 TUI）。
- 沙箱开放 `/tmp`、`/private/tmp`（N9）。
- **端到端（WSL tmux，真实 deepseek，tmux 后端）**：Team 全生命周期通过——TeamCreate → Agent(team_name) spawn bob（split pane + team-member 子进程自治循环）→ worktree 落地 → 任务执行（/tmp/bob.txt=step1）→ bob TeamSendMessage 通知 lead → **Lead 自动唤醒**（bob idle 无需输入自动处理）→ TeamSendMessage 续派（step2 追加成功）→ /team list/info/delete --force 清理。

### 配置
- `.novacode/config.yaml`：`context_window: 1000000`（deepseek-v4-flash 原生 1M token，默认 openai 协议 128K 会频繁压缩）。ch13 可选顶层 `enableSubAgentBackground: false` 关闭后台（缺省 true）。ch15 可选顶层 `features: {coordinator_mode: true, fork_teammate: false}`。

## 4. 当前卡点 / 未完成

- **没有阻塞性 bug**。ch15 已完成（单测 750 passed + ruff/格式全绿 + `mypy team` 全绿，WSL tmux 端到端四场景全过 + in-process 续派/delete 实跑）。ch14 已完成（WSL tmux 端到端场景 1/2/4/6 全过、3/5 核心过）。ch13 已完成（tmux 端到端 13/13）。
- **ch15 全后端已实跑**：in-process 通过「临时 `mv /usr/bin/tmux /usr/bin/tmux.bak` + `env -u TMUX`」让 `detect_backend` 走 in-process（客户端用 tmux.bak 交互）实跑通过——含**续派 step2（`task_mgr.send_message` 路径）+ `/team delete --force`**；Coordinator/Plan 审批均实跑通过。**iterm2 后端仍仅单测命令构造**（macOS only，无法在本机实跑）。
- **ch15 checklist 已全面验收（2026-08-13 补全）**：实现完整性 17/17、集成 20/20、编译测试 5/5（含 `mypy team` 全绿）、端到端四场景全过、失败回归 6/6（补了 disk 写失败、tmux split 失败单测）。无未验收项。
- **ch14 checklist 已全面验收（2026-08-11 补全）**：symlink 用 WSL 脚本复验通过；session 重启恢复 active_cwd 用 tmux 两轮实跑通过；`/worktree remove` 与 exit 后二次读取（强制工具调用 → `文件不存在`）均实跑通过。无未验收项。
- **mypy 存量错误**：裸跑 `mypy src/novacode` 报约 123 个错误（tui/run.py termios、cli.py:139 validate_tools 等**既有代码**），ch14 新增模块已修干净；HANDOFF 此前声称「mypy 全绿」不准确。
- **无待修项**。场景 10 的「adopt 卡 running + result 空」已定位为 `asyncio.wait_for` 超时 cancel 子 Agent 协程，修复（`asyncio.wait` 不 cancel）后复验后台正常跑完、result 正常；期间出现的 deepseek 偶发 400 与 adopt 无关（复验会话 0 个 4xx，见 §6.6 坑 30）。
- **未端到端实跑**：InstallSkill 的 tmux http server 完整链路（核心逻辑已被单测覆盖：zip-slip/happy flow/reload）。
- **skill 命令带参数未做**：ch14 已把 `parse` 改为三元组透传 tail 且 `Command` 有 `args_handler` 通道，但 skill 命令仍零参数——给 skill 命令加 `args_handler`（尾随文本作为 args 传给 executor）即可支持 `/frontend-design xxx`，用户提过希望支持，未要求本期做。
- `/review`、`/commit` 等 Skill 命令依赖真实 LLM，网络慢时 fork 子 Agent 可能跑 1-3 分钟（超时兜底已加）。
- **子 Agent Ask 无弹窗**：`approval_upgrader` 默认拒绝回灌（见 §3 ch13 设计决策 3）。将来若想恢复弹窗，需给工具执行加「穿透主 Agent 阻塞的 side-channel」或改事件模型——接口已留 `ok=False` 分支。
- **`model` 不切 provider**（见 §3 ch13 设计决策 1），子 Agent 恒用父模型。

## 5. 下一步计划

- **ch15 Agent Team 已完成**（docs/ch15 四份文档经修订审批后开发，全量单测 744 passed + ruff/格式全绿，WSL tmux 端到端 Team 全生命周期通过）。ch14、ch13 已完成。
- 可选推进（ch15 已有基础设施，用户未明确要求）：
  - `/team` 命令已支持 list/info/delete/kill；后续可加 `/team enter` 进入团队视角、队员间任务指派优化。
  - Plan 审批（F48-F51）已实现核心（ingest_team_mailbox 切模式），完整端到端流程（planner 角色 + plan_mode_required）未实跑，可补。
  - Coordinator Mode（双锁）已实现，未在真实 TUI 验证 `[COORDINATOR]` 标签与写工具剥夺，可补。
- 可选增强（用户未明确要求，可提议）：
  - skill 命令支持参数（ch14 的 parse 已透传 tail，给 skill 命令加 `args_handler` 即可）。
  - `/skill` 展示 source/mode 元信息。
  - bash 工具更完善的编码处理。
  - 多 provider + 子 Agent 按 `model` 切 provider（改造点见 §3 ch13 设计决策 1）。
  - hook subagent 动作对接 SubAgent 系统（当前占位 stub）。
  - Worktree 相关：跨 novacode 进程实例共享（当前单实例）、Worktree 间合并策略、`sweep_stale` 在会话结束时兜底触发。

## 6. 踩过的坑（绝对不要再踩，按优先级）

### 6.1 Windows 平台坑
1. **`sys.platform` 在 Windows（含 64 位）恒为 `"win32"`**——判断 Windows 用 `sys.platform == "win32"` 或 `os.name == "nt"`；要区分 CPU 位数才用 `platform.machine()`。不要再问「win64 呢」。
2. **`Path.home()` 在 Windows 不读 `HOME` 环境变量**（读 USERPROFILE）——测试里 `monkeypatch.setenv("HOME", ...)` 无效！正确做法：代码里封装独立函数（如 `catalog._home()`），测试 monkeypatch 该函数。
3. **bash 工具在 Windows 用 `asyncio.create_subprocess_shell` 实际走 cmd.exe**，不是 bash——AI 写 `ls/tail/find` 全失败。修复：`description` 按平台提示 AI 用 cmd 语法（dir/type/findstr）；输出按 GBK 解码（Windows cmd 默认系统 ANSI）。
4. **bash 工具 Windows 输出编码**：`_decode` 用 gbk 优先（主场景），UTF-8 输出是例外不保证完美（`"你好".encode("utf-8")` 能被 GBK 解成别的字，不报错）。

### 6.2 prompt_toolkit 坑
5. **`Completion.start_position` 必须 ≤ 0**（负值=向左替换），且要**动态计算** = `-(「/」后光标前字符数)`。固定 -1 会：输入 `/` 时丢斜杠、输入 `/he` 时残留成 `/hhelp`。
6. **`Completion` 用 `display=` 参数**（不是 `display_formatted_text`，ptk 3.0.53 API）。
7. **补全菜单初始无高亮**（complete_index=None），第一次 ↓ 才选中第一个候选——ch10 checklist C3/C4 描述据此调整过。
8. 非全屏渲染：任何 stderr 输出都会污染输入框——日志已重定向到 `.novacode/logs/novacode.log`（`_setup_logging`），**不要往 stderr 打内容**。

### 6.3 agent / fork / 压缩坑
9. **`/exit` 顺序 bug**：`dispatch_slash` 返回 True 后 `continue` 会先画输入框再查 exit_flag——**主循环开头必须查 exit_flag**（`while True: if exit_flag[0]: break`）。
10. **fork 子 Agent 无 UI 响应 approval 会卡死**——必须用 `Mode.BYPASS`（Skill 的 allowed_tools 已收窄工具集，权限黑名单/沙箱对 BYPASS 仍生效）。
11. **fork 子 Agent 必须继承主对话 context_window**（默认 200000 硬编码会与主窗口错乱导致压缩频繁）；加总超时 `FORK_TIMEOUT=180s` 兜底（LLM 卡住时不卡主对话）。
12. **循环导入**：`skills/executor.py` 里 `from ..agent import Agent` 必须函数内延迟导入（agent → skills.adapter → skills/__init__ → executor → agent 会循环）。
13. **`Mode` 是 IntEnum**，`.value` 是整数（0/1/2/3）——展示用 `str(mode)`（返回 default/plan/acceptEdits/bypassPermissions）。

### 6.4 SKILL.md / parser 坑
14. **`yaml.safe_load` 的 `ParserError` 不是 ValueError**——不 catch 会让 Catalog 启动崩溃。已修复：parse 层把 YAMLError 转 ValueError。
15. **SKILL.md 常带 BOM/CRLF**（Windows PowerShell 落盘引入）——parser 必须 strip BOM + 归一化 CRLF。**BOM 来源是落盘工具（PowerShell），不是 skills.sh/GitHub 源文件**。
16. **市场规范用连字符 `allowed-tools`**（anthropics/skills），也要认下划线 `allowed_tools`。
17. **name 上限放宽到 64**（市场规范），不要用 32 误拒第三方 skill。
18. **测试污染**：真实 `~/.novacode/skills/`（用户装了 frontend-design）会污染「仅内置」的 Catalog 测试——这类测试必须 monkeypatch `_home` 到 tmp_path。

### 6.5 环境 / 测试坑
19. **venv 是 uv 创建的（无 pip）**——装包用 `uv pip install <pkg> --python .venv/Scripts/python.exe`。
20. **WSL tmux 端到端**：
    - `wsl -d Ubuntu -- bash -lc '...'` 输出中文乱码（stdout 编码）——tmux 输出 `capture-pane -p > 文件` 后从 Windows 侧 Read 文件，避免 wsl stdout 编码。
    - **启动后 sleep 至少 10-12 秒再发命令**——实测 sleep 6 秒时 Enter 仍被吞（命令显示在输入框但不提交）；12 秒稳定。若怀疑被吞，先发 `hi` Enter 探测是否回复。
    - **Git Bash 会把裸 `/mnt/d/...` 参数转成 Windows 路径**——`wsl -- bash /mnt/d/x.sh` 会报 No such file。用 `MSYS_NO_PATHCONV=1 wsl -d Ubuntu -- bash /mnt/d/x.sh`。
    - bash 命令引号嵌套容易坏——用 Write 工具写 `.sh` 脚本再执行。
    - WSL 里 `_home()` 是 `/root`，看不到 Windows 用户目录的 skill——验证用户级 skill 要在 Windows 本机跑 python。
    - **关键交互键位**：Ctrl+O 展开思考（0x0f，可重复触发）；Ctrl+C 取消（0x03）；tmux 用 `send-keys C-o` / `C-c` 发送。审批读键 1/2/3 直选。
21. **pytest**：`asyncio_mode = "auto"`（无需手动 @pytest.mark.asyncio 于所有 async 测试，但部分已有）。
22. **WSL 已装 ruff**（系统 python3，`--break-system-packages`，ch12 场景 3 用）；**jq 未装**——hook 命令提取 path 用 `python -c "import sys,json; print(json.load(sys.stdin)['tool_input']['path'])"` 替代。
23. **权限放行含 `/` 的命令用 `Bash(**)` 而非 `Bash(*)`**：`match_pattern` 对含 `/` 的目标按路径分段，`*` 只匹配单段（`Bash(*)` 匹配不了 `ls /mnt/...`）。

### 6.6 ch13 SubAgent 坑（绝对不要再踩）
24. **未调度的 asyncio.Task 被 cancel 时不执行协程体的 finally**——`create_task(runner())` 后若在 runner 首次调度前 `handle.cancel()`，runner 的 try/finally 全部不跑，`done` 队列不会收到通知。`task.Manager.stop` 已处理：cancel 后 `await handle` + 若 status 仍 RUNNING 手动补终态 + push done。新增类似「create_task 后立刻可能 cancel」的逻辑必须照此兜底。
25. **run_to_completion 的 events 聚合器别用 cancel 收尾**（会丢 usage/tool 统计）——`launch_fork` 里先 `await events.put(None)`（sentinel）让聚合器消费完自然退出，再 `await collector`。之前用 `collector.cancel()` 导致 fork usage 偶发丢 0。
26. **`_execute_batched` 串行分支插 approval_upgrader 时，upgrader 决定 ALLOW 后必须跳过第五层**——否则 `asyncio.wait([respond, watcher])` 永远挂起（respond 无人响应）。实现用 `upgrade_decided` 标志把「ch12 Notification + 第五层」包进 `if not upgrade_decided:`。
27. **主 Agent 调 Agent 工具在 default 模式下触发 Ask**（Agent 工具非只读，归命令执行类）——端到端/集成测试模拟「已批准」用 `Mode.BYPASS` 跑主 Agent，否则测试卡死在等 respond。
28. **`SessionRuntime` 是 dataclass，不能 `SessionRuntime(200_000)`**——完整关键字构造（replacement/recovery/auto_tracking/session/context_window），`new_session_context` 从 `novacode.compact` 导入。
29. **Agent 工具没有 ToolContext**——`tool.Tool.execute(args: str) -> Result` 只收 raw JSON 字符串。子 Agent 无法在运行时感知「调用者是谁」，所以嵌套阻断只能靠构造期工具过滤；Fork 路径的主对话历史靠 `parent._active_conv`（`Agent.run` 每轮开头记录当前 conv）。
30. **前台超时切后台（adopt）——ch13 场景 10 实测**：① 最初用 `asyncio.wait_for` 超时会 **cancel** 子 Agent 协程，导致重跑时 conv 被中断污染、result 为空——**必须改 `asyncio.wait([run_task], timeout)` 不 cancel**、adopt 接管同一 run_task（已修，复验后台正常跑完、result 正常）；② adopt 时 `bt.handle` 必须保持 run_task 本身（stop 才能取消子 Agent），runner 只是包装 await handle 写终态，**不能 `bt.handle = create_task(runner())` 覆盖**；③ 期间偶发的 deepseek `HTTP 400` 与 adopt **无关**（复验会话 0 个 4xx，全 200）；④ 后台任务完成可能很慢（bash 长命令 + deepseek 生成需 1-3 分钟），「卡 running」先等够再判定，别急着下结论。
31. **主 Agent 首次调 Agent 工具在 default 模式触发审批**（Agent 工具非只读，归命令执行类 Ask）——实跑确认。测试/验收时选「允许本次」即可，或 `settings.local.yaml` 放行 `Agent`（`permissions.allow: [Agent]`）免后续审批。集成测试用 `Mode.BYPASS` 跑主 Agent 模拟已批准。
32. **tmux 驱动时序**：`send-keys '1'` 批准审批弹窗时，若审批已在按键前结束，'1' 会落入输入框（累积成 `1111...`）污染下一条消息——检测到「是否继续」就立即按键、且之后用 `C-u` 清输入框；实测按键落空概率高，优先在 `settings.local.yaml` 放行 Agent 工具减少审批。
33. **Agent 定义文件的 `disallowedTools` / `tools` 必须写内部工具名，不认 friendly 名**：工具过滤（`apply_agent_tool_filter`）按 registry 内部名（小写下划线）匹配——`write_file`/`edit_file`/`bash`/`read_file`/`glob`/`grep`/`Agent`。写 `WriteFile`/`EditFile`/`Bash`/`Read`（friendly 名，映射见 `permission/settings.py` `_FRIENDLY`）**不生效**，该工具仍对子 Agent 可见。安全含义：`permissionMode: bypassPermissions` + disallowed 写错名 → 子 Agent 实际能写文件/执行命令，与定义 body 声称的「只读」矛盾（用户 security-reviewer 案例）。**最佳实践**：纯只读子 Agent 直接用 `permissionMode: plan`（`Agent.run` 自动只导出只读工具，无需手写 disallowed）；需手写时务必用内部名。`model` 字段同理——解析通过但本期不切 provider，恒用父模型。

### 6.7 ch14 Worktree 坑（绝对不要再踩）
34. **`Manager.current_session` 是实例属性不是方法**——同名方法已删，调用 `current_session()` 会 `'NoneType' object is not callable`（ch14 端到端启动即踩）。读取/测试都用 `mgr.current_session`。
35. **变更检查必须忽略 setup 条目**：post-creation setup（spec F7/F9）会把 `.novacode/config.yaml`/`settings.local.yaml` 复制、把 `.venv`/`node_modules`/`vendor` 软链进 worktree——它们在 `git status --porcelain` 里是未跟踪条目，不忽略会导致**只读子 Agent 也被判定有变更**、auto_cleanup 保留该删的 worktree（ch14 场景 2 首测即踩）。`git.py::_is_setup_entry` 已统一过滤，改 sweep/变更检查时务必复用。
36. **slash 命令驱动时序（tmux）**：命令 handler 执行（尤其 `create` 的 `git worktree add` 在 WSL /mnt/d 可能 10-20s）期间，后续 send-keys 的 Enter 会被吞、命令累积进输入框。实测：命令间隔 ≥10s 稳定；`create` 后用更久，或把 worktree 在**启动前**外部 `git worktree add`（启动时 Manager scan 自动识别，`/worktree enter` 即纯内存操作）。检测被吞：capture-pane 里 `❯` 后有多行未提交文本。
37. **Windows `subprocess.run` 必须 `encoding="utf-8"`**：git 输出恒 UTF-8，`text=True` 走 GBK 解码（中文 Windows）会 reader 线程崩 → stdout 为 None → `'NoneType'.strip()` AttributeError。worktree 包所有 `subprocess.run` 已加 `encoding="utf-8"` + `check=False`；bash 工具同理用 `_decode`（GBK 优先）。
38. **worktree 里 `.git` 是文件不是目录**：内容 `gitdir: <私有 gitdir>`。`_resolve_head_sha_from_fs` 读它 → `<gitdir>/HEAD`（`ref: refs/heads/<name>`）→ 分支 ref 实际在 **common dir**（读 `<gitdir>/commondir`，缺省为私有 gitdir 父级）下，不在私有 gitdir。fs 解析失败返回 None，head_commit 置空由 fail-closed 兜底。
39. **`git worktree add` 的 base 需要 HEAD commit**：空仓库（刚 git init 无提交）`git worktree add ... HEAD` 失败。端到端前先 `git init -b main` + 首次 commit（`.novacode/` 用 `:(exclude).novacode` 排除，避免运行时产物进版本控制）。
40. **非 git 目录下 Manager 降级**：novacode 启动在非 git 目录时 Manager 抛 ValueError → cli 降级 None（stderr 警告）→ `/worktree` 命令报「Worktree 未启用」、isolation=worktree 子 Agent 报 `worktree manager not configured`。D:\NovaCode 已 git init（本地仓库，无 remote），worktree 功能可用。

### 6.8 ch15 Agent Team 坑
41. **WSL 里 `detect_backend` 恒返回 tmux**：WSL 有 tmux 二进制，`shutil.which("tmux")` 非空 → F14 第三步命中。即使 `unset TMUX`，只要在 tmux 会话内启动（`$TMUX` env 存在）就走 tmux 后端。真实 in-process e2e 在 WSL 无法做，用单测覆盖（test_spawn_inprocess_member 真实 Agent+worktree）。
42. **主 Agent 同时有 ch13 `SendMessage` + `TeamSendMessage`**：LLM 给队员发消息时可能误用 ch13 的 `SendMessage`（后台任务版，参数 name）而非 `TeamSendMessage`（参数 to），触发后台任务审批后报「no background task named xxx」。已改进两个工具 description 区分；弱模型仍可能选错，prompt 里明确「队员通信用 TeamSendMessage」。
43. **跨进程 `is_active` 内存/disk 不一致**：Pane 后端子进程 `set_member_active` 写 disk，主进程内存 team 不主动 reload → `/team info` 显示的内存态可能过时（disk 正确，重启 scan 还原）。`Team.set_member_active` 内部 reload-before-modify，跨进程写不丢。
44. **`team/tools/*.py` 引用 agent 包必须 `from ...agent`（三点）**：tools 在 `novacode.team.tools`，`from ..agent` 会解析成 `novacode.team.agent`（不存在）→ `No module named 'novacode.team.agent'`。team 包本体（manager/spawn/member）用 `from ..agent`（两点）。spawn/member 顶层 import agent 时注意 `novacode.team` 与 `novacode.agent` 的层级。
45. **`TeamSendMessage` 权限校验**：`plan_approval_response` 仅 Lead 可发（ctx 无 TeammateContext = Lead）、`shutdown_response` 只能发给 Lead。队员发 plan_approval_response 被拒（`仅 Lead 可发`）。
46. **`tmux split-window` 的 pane_id 是 `%N`**：TmuxBackend.spawn 用 `-P -F "#{pane_id}"` 捕获 pane id（如 `%1`），写入 TeammateInfo.pane_id 供 wake（`tmux send-keys -t %1`）。子进程跑 `python -m novacode --team-member`（F19a），pane 内是只读日志流（`● tool(args)` 打印），不构造 TUI。
47. **工具执行超时会损坏 conv（ch15 e2e 踩坑）**：`Agent._run_one` 的 `asyncio.wait_for(..., tool.DEFAULT_TIMEOUT=30)` 超时若直接抛 TimeoutError，`_execute_batched` 的批结果不完整 → `add_tool_results` 用残缺结果 → assistant tool_calls 无 tool_result 闭合 → 后续所有请求 400（`assistant tool_calls must be followed by tool messages`）。触发：Lead 用 `bash sleep 30` 凑时间（恰好 = DEFAULT_TIMEOUT）。已修：`_run_one` catch TimeoutError 转结构化错误 + `_safe_task_result` 兜底工具异常。**任何给工具执行加超时/异常路径的改动，必须保证 tool_calls 用结果闭合**。
48. **WSL 真实 in-process e2e 技巧**：`detect_backend` 在 WSL 恒返回 tmux（`which tmux` 命中）。要真跑 in-process：`mv /usr/bin/tmux /usr/bin/tmux.bak`（detect 的 which 找不到 → in-process）+ 客户端改用 `/usr/bin/tmux.bak` 交互 + `env -u TMUX` 启动 novacode；脚本 `trap` 恢复 tmux。

## 7. 环境与常用命令

```
# 本机（Windows）开发
.venv/Scripts/python.exe -m pytest -q          # 全量测试（当前 750 passed + 2 skipped）
.venv/Scripts/python.exe -m ruff check src/novacode tests
.venv/Scripts/python.exe -m ruff format src/novacode tests
uv run novacode                                 # 启动（读 .novacode/config.yaml，DeepSeek）

# WSL 端到端
wsl -d Ubuntu -- bash -lc 'cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode'
# tmux 交互测试：capture-pane -p > 文件，Windows 侧 Read
```

**测试前注意**：`~/.novacode/skills/`（Windows）已有用户手动装的 `frontend-design`；`.novacode/sessions/`、`.novacode/memory/` 会随运行重建，测试后清理以免污染。`D:\NovaCode` 已是本地 git 仓库（ch14 端到端前 `git init -b main` + 首次提交，无 remote）；`.novacode/settings.local.yaml` 放行了 `Agent` 工具（主 Agent 调子 Agent 免审批，HANDOFF §6.6 坑 31/32 建议）；`.novacode/agents/worktree-writer.md` 是 ch14 端到端测试 Agent（isolation: worktree）。

## 8. 交接人备注

- 用户开发偏好：中文回复/注释；安装工具前先征求同意；发现文档与实际不符时先修订文档待审批再开发；端到端测试在 WSL tmux 按 checklist 验收。
- 每一章开发流程：读 docs/ch0X 四份文档 → 修订与实际对齐（Textual→prompt_toolkit、UI Protocol→CommandContext 等）→ 用户审批 → 按 task.md 开发 → tmux 端到端。
- 最近一次对话的收尾工作：bash 工具 Windows 适配（description 分平台 + GBK 解码）、SKILL.md parser 加固（BOM/CRLF/YAMLError/allowed-tools 连字符/name 64）。这些都已测试（417 passed）。
