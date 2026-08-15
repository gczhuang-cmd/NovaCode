# SubAgent 机制 Spec

> 修订记录（2026-08-11，对照实际代码）：
> 1. **TUI 架构**：原稿按 Textual 写（`novaCodeApp`/`on_mount`/`BINDINGS`/ESC 切后台）；实际是 **prompt_toolkit 非全屏 + Rich**，主循环在 `tui/run.py::run()`。所有 Textual 引用删除。
> 2. **Skill fork 位置**：原稿写 `tui/skill_fork.py`；实际 Skill fork 在 **`skills/executor.py::Executor._run_subagent`**，改造目标改为它。
> 3. **Tool 协议无 context**：实际 `tool.Tool.execute(args: str) -> Result`（args 是 raw JSON 字符串，**没有 ToolContext**），「从 ctx 检测调用方来源 / 拿父对话」整套机制无法落地。**嵌套阻断统一收敛为工具过滤**（`ALL_AGENT_DISALLOWED_TOOLS` 对定义式与 Fork 都剔除 Agent 工具）；`is_fork_context` 降级为辅助函数，不作运行时拦截闸。
> 4. **子 Agent Ask 升级主 TUI 弹窗本期不实现**：前台 inline 子 Agent 阻塞在主 Agent 工具执行内，事件流暂停，无法弹窗。改为 `approval_upgrader`（默认实现拒绝本次、结果回灌，子 Agent 自行调整）。`dontAsk` 兜底仍实现。
> 5. **ESC 切后台本期不实现**：实际 ESC 已绑定取消（`_CancelWatcher`）。保留「显式后台 + 超时自动后台」两条路径。
> 6. **`model` 字段不切 provider**：本项目单 provider（deepseek），无按模型切换的 provider 配置。`model` 字段在 schema/frontmatter 保留，本期解析但不切换。
> 7. **类型命名对齐**：`PermissionMode→Mode`、`PermissionOutcome→Outcome`、`PermissionDecision→Decision`、`parse`→`parse_mode(s) -> (Mode,bool)`、`ApprovalRequest` 定义在 `agent/__init__.py`（字段 `name/args/reason/respond: asyncio.Future[Outcome]`）。
> 8. **`ALL_AGENT_DISALLOWED_TOOLS` 扩大**：含 `Agent + TaskList/TaskGet/TaskStop/SendMessage`（task 元工具对子 Agent 不可见）；task 工具 `is_system=False`。原稿「task 工具 is_system=True 让子 Agent 豁免可见」与「子 Agent 不该看到元工具」矛盾，一并修正。
> 9. **Fork Boilerplate 注入方式调整**：`build_forked_messages` 追加单条 user = `FORK_BOILERPLATE`；任务文本由 `run_to_completion` 统一 `add_user`（保证不重复注入）。

## 背景

novaCode 目前是单 Agent 架构：所有任务在同一个对话上下文里执行。这导致两个问题：

1. **上下文污染**：长任务后再做无关任务，前序中间结果（读过的文件、diff、错误回放）成为后续任务的噪声，token 飙升、响应质量下降
2. **无法并行**：没有把独立子任务分发出去并行执行的机制，主对话被长任务阻塞

novacode 已经有「子 Agent 雏形」：

- ch11 Skill fork 模式通过 `skills/executor.py::Executor._run_subagent` 构造受限子 Agent，用 `sub.run(fork_conv, Mode.BYPASS, cancel)` 跑完一轮，返回末尾 assistant 文本写回主对话
- `Conversation.from_messages` / `replace_history` 已支持深拷贝消息列表
- ch12 Hook 引擎、`SessionRuntime.pending_reminders`（reminder 注入通道）已就绪

但还缺：
- 没有统一的、可被主 Agent 主动调用的 **Agent 工具**——子 Agent 只能由 Skill fork 触发
- 没有 **角色定义文件** 加载机制（Agent 角色全部写死在 fork 闭包里）
- 没有 **后台任务管理**——所有子 Agent 当前都是阻塞前台模式
- 没有 **工具过滤多层防线**——子 Agent 理论上可以无限嵌套
- Skill fork 与未来 SubAgent 工具两套代码并存

本章把上述能力补齐，让 novacode 从单 Agent 进化到可分发任务的主从架构。

## 目标

- **G1**：提供统一的 Agent 工具，主 Agent 通过 `subagent_type` 参数选择预定义角色或留空走 Fork 路径；工具列表对模型始终稳定（不因角色定义增减而变化）
- **G2**：子 Agent 拥有独立的运行时状态——**消息**、**token 计数**、**文件读缓存**（recovery）；共享基础设施——LLM 客户端、Hook 引擎、文件系统、`tool.Registry`、`permission.Engine`
- **G3**：支持两种创建模式：
  - **定义式**：指定 `subagent_type`，从空白对话 + 预定义角色 system prompt 启动
  - **Fork 式**：不指定 `subagent_type`，克隆父对话历史并注入 Fork Boilerplate，借 prompt cache 降首次请求成本
- **G4**：角色定义为 Markdown + YAML frontmatter 文件；支持多来源加载，优先级：项目级 > 用户级 > 内置；同名定义按 source 优先级覆盖，前者覆盖后者
- **G5**：子 Agent 以 **RunToCompletion** 模式执行——任务直接注入对话，模型不再调工具即结束，返回最后一条 assistant 文本作为结果
- **G6**：子 Agent 在工具调用时遇到权限判定，按 **两层链** 处理：① 共享 `permission.Engine`（含父对话已批准的本地层账本）→ ② 角色 frontmatter 的 `permissionMode` 兜底（`dontAsk` 直接放行）。仍剩 Ask 时经 `approval_upgrader` 处理（默认拒绝本次并回灌，子 Agent 调整；本期不做弹窗）
- **G7**：支持后台任务：两种进入方式——① 显式 `run_in_background:true`、② 前台超时 120 秒自动切后台；Fork 路径无条件后台；Fork Boilerplate 注入到子 Agent 首条 user 消息约束其行为
- **G8**：后台任务跑完通过 `<task-notification>` 自动注入主对话（主 Agent 下次 turn 即看到）；主 Agent 可通过 `TaskList`/`TaskGet`/`TaskStop` 工具主动查询和操控，可通过 `SendMessage` 给已跑完的、仍存活的后台 Agent 续派任务
- **G9**：工具过滤多层防线阻断子 Agent 无限嵌套——全局禁止列表（任何子 Agent 永远不能用 Agent / TaskList / TaskGet / TaskStop / SendMessage 元工具）、后台白名单（后台 Agent 只能用基础读写网络工具）、定义层 `tools`/`disallowedTools` 业务约束
- **G10**：复用 SubAgent 底座统一 Skill fork 路径——`skills/executor.py::Executor._run_subagent` 改为调用 `agent.launch_fork` 公共启动函数，两条路径走同一段 agent 构造逻辑
- **G11**：内置 3 个角色——`general-purpose`（全工具）、`Explore`（只读探索）、`Plan`（只读规划）；`SourcePlugin` 占位保留，本期不实现真插件加载，加载顺序里插件来源恒为空

## 功能需求

### Agent 工具

- **F1**：新建 `Agent` 工具，参数(JSON Schema)：
  - `prompt`(string,必填)：交给子 Agent 的任务指令
  - `description`(string,必填)：一句话描述任务，供 UI 展示
  - `subagent_type`(string,可选)：指定预定义角色名，留空时走 Fork 路径
  - `model`(string,可选)：schema 保留 `haiku` / `sonnet` / `opus` / `inherit`；**本期解析但不切换 provider**（单 provider 环境，恒用父 provider），后续接入多 provider 时启用
  - `run_in_background`(bool,可选)：true 时强制后台启动；Fork 路径忽略此字段（无条件后台）
  - `name`(string,可选)：给本次启动的子 Agent 命名，供 SendMessage 用；同名后启动的覆盖前面的弱引用
- **F2**：Agent 工具的 `execute`：
  - subagent_type 非空：`catalog.resolve(name)` 取定义；不存在则返回结构化错误「未知 subagent_type: X」
  - subagent_type 为空：走 Fork 路径，从 `catalog.fork_definition()` 取临时定义（name=`__fork__`）
  - 按 `run_in_background` 与 Fork 强制规则，选择 inline 跑（阻塞返回 final_text）或 background 跑（返回 `{task_id, status:"async_launched"}`）
- **F3**：Agent 工具被全局禁止列表 `ALL_AGENT_DISALLOWED_TOOLS` 标记——任何子 Agent 的工具过滤都会剔除它，从根源上断绝嵌套

### Agent 定义文件

- **F4**：Agent 定义文件是 Markdown，以 `---` frontmatter 块开头、紧跟正文（子 Agent 系统提示）；frontmatter YAML 字段：
  - `name`(必填)：角色名，`^[A-Za-z][A-Za-z0-9\-_]{0,31}$`（大小写都允许，对齐 `Explore`/`Plan`）
  - `description`(必填)：一句话描述，用于 Agent 工具的 `subagent_type` 文档与 UI 列表
  - `tools`(可选,list[str])：工具白名单
  - `disallowedTools`(可选,list[str])：工具黑名单
  - `model`(可选)：`haiku` / `sonnet` / `opus` / `inherit`，缺省 `inherit`；**本期仅解析，不切换 provider**
  - `maxTurns`(可选,int)：最大迭代轮数，缺省继承全局 `MAX_ITERATIONS=25`
  - `permissionMode`(可选)：`default` / `acceptEdits` / `plan` / `bypassPermissions` / `dontAsk`，缺省 `default`；`dontAsk` 是子 Agent 专属——自动批准所有规则未命中的工具（黑名单/沙箱仍拦）
  - `background`(可选,bool)：缺省 false；true 时 Agent 工具忽略 `run_in_background` 参数、强制后台
- **F5**：Catalog 三层加载（本期插件级恒为空），顺序：
  1. 项目级：`<root>/.novacode/agents/*.md`
  2. 用户级：`~/.novacode/agents/*.md`
  3. 内置级：随包发布的 `novacode/subagent/builtin/*.md`（通过 `importlib.resources` 读取）
- **F6**：同名定义按 source 优先级覆盖——项目级 > 用户级 > 内置级；`resolve(name)` 返回优先级最高的版本
- **F7**：Catalog 启动期加载，加载失败的单个文件（frontmatter 不合法、字段错）走 stderr 警告并跳过，不阻断启动；`model`/`permissionMode` 非法值在加载期 stderr 警告并降级到 `inherit`/`default`
- **F8**：本章不引入插件加载器——`SourcePlugin` 常量保留供未来扩展；加载顺序里第四层恒为空列表

### 子 Agent 运行时

- **F9**：扩展 `agent.Agent` 增加 `async def run_to_completion(self, conv, task, events=None) -> str` 方法：
  - 把 `task` 作为 user 消息追加到 conv（`if task:` 非空才追加，避免 Fork 已装填时重复）
  - **复用主对话 `run` 的同一段循环代码**（`async for ev in self.run(...)` 收集），不重复实现
  - 每轮请求前把 tool / usage 事件转发到可选 `events` 队列（TaskManager 聚合 tool_count / last_activity 用）
  - 模型不再调工具时结束循环，返回 **conv 末尾 assistant 文本**
  - 触达 `max_turns` 时返回最后一条 assistant 文本 + 抛 `MaxTurnsReached` 错误
  - 子 Agent 不触发 memory update（构造时不传 `memory_manager`，`_maybe_trigger_memory_update` 自然跳过）；hook 调度仍生效（复用 run 内的 `_dispatch_hook` 点）
- **F10**：新增 Agent 构造关键字参数（追加在现有 `*` 关键字参数之后）：
  - `system_prompt: str | None`：子 Agent 启动时覆盖默认系统提示（非空时 `run` 内 `sys_text = self.system_prompt or build_system_prompt(...)`）
  - `max_turns: int`：0 表示用全局 `MAX_ITERATIONS=25`（`run` 的循环上限改为 `self.max_turns or MAX_ITERATIONS`）
  - `permission_mode: Mode | None`：None 时 `run_to_completion` 用 `Mode.DEFAULT`；非 None 作为 `run` 的 mode 入参（含 defs 只读筛选）
  - `dont_ask: bool`：True 时 `_execute_batched` 串行分支的 Ask 直接短路为 Allow（黑名单/沙箱/deny 规则仍拦）
  - `approval_upgrader: ApprovalUpgrader | None`：Ask 升级回调；None 时走「默认拒绝本次」路径
  - （provider 无需新增参数——子 Agent 构造时直接传父 provider）
- **F11**：子 Agent 的运行时状态隔离——独立 `SessionRuntime`、独立 `Conversation`、独立 token 计数；但共享 `Provider`、`Registry`、`PermissionEngine`（同一实例）、`HookEngine`

### 权限决策

- **F12**：子 Agent 工具调用权限决策链（在 `_execute_batched` 内分支）：
  1. 共享 `permission.Engine` 前四层（黑名单 → 沙箱 → 三层规则集 → 模式兜底）——父对话已批准的本地层规则自动生效
  2. 子角色兜底——`dont_ask` 模式把规则未命中的 Ask 直接 Allow；`permission_mode=acceptEdits` 放行写、`bypassPermissions` 全 Allow（黑名单/沙箱仍拦）经 `engine.check(mode, ...)` 的 mode 兜底天然生效
  3. 仍剩 Ask → 调 `self.approval_upgrader(req)`；**默认实现返回「拒绝本次」**，拒绝原因回灌给子 Agent 调整（本期不做主 TUI 弹窗）
- **F13**：`ApprovalUpgrader` 类型：`Callable[[ApprovalRequest], Awaitable[tuple[Outcome, bool]]]`——返回 `(outcome, ok)`；`ok=False` 时调用方走默认拒绝路径。`approval_upgrader=None` 时等价于默认拒绝。

### 后台任务管理

- **F14**：新建 `task.Manager`，持有 `dict[str, BackgroundTask]`，提供 `launch(ag, conv, name, task)`、`get(id)`、`list()`、`stop(id)`、`send_message(name, msg)`、`subscribe_done() -> asyncio.Queue[str]`、`upgrade_approval(req)`（本期默认拒绝）
- **F15**：`BackgroundTask` 字段：
  - `id`(str，manager 生成)
  - `name`(str，可选，F1 的 `name` 字段)
  - `sub_agent`(Agent)
  - `conv`(Conversation，子对话)
  - `task`(str，初始任务文本)
  - `status`(`running` / `completed` / `failed` / `cancelled`)
  - `result`(str，跑完后填)
  - `err`(BaseException | None)
  - `start_time` / `end_time`
  - `cancel_event`(asyncio.Event)
  - `handle`(asyncio.Task，跑动协程，Stop 时 cancel())
  - `usage`(TokenUsage，token 计数)
  - `tool_count`(int，工具调用次数计数器)
  - `last_activity`(str，最近一次工具名)
- **F16**：`launch` 内部 `asyncio.create_task`：`sub_agent.run_to_completion(conv, task, events)` → status 终态 → 推 `task_id` 到 `done` 队列 → TUI 主循环下次注入 `<task-notification>`
- **F17**：两种进入后台的方式：
  1. **显式**：Agent 工具 `run_in_background:true` → 直接调 `launch`，工具 result 立刻返回 `{task_id, status:"async_launched"}`
  2. **超时自动**：Agent 工具默认前台 inline 跑，但前台 run 启动后 `asyncio.wait_for(timeout=AUTO_BACKGROUND_SECONDS)`（常量 120 秒），超时则重新起协程继续跑并调 `manager.adopt_running(...)` 接管，Agent 工具 result 改返回 `{task_id, status:"timed_out_to_background"}`
  - （ESC 手动切后台**本期不实现**：实际 `_CancelWatcher` 中 ESC 已绑定取消主 Agent 回复）
- **F18**：Fork 路径无条件后台（代码内 override，忽略 `run_in_background`）
- **F19**：后台任务完成时，Manager 把 `task_id` push 到 `done` 队列；TUI 在主循环每轮（收到新输入、`run` 之前）排空队列，把如下文本作为 system reminder 拼到主对话下一次 reminder 区（不打断当前对话）：
  ```
  <task-notification>
  Task X (name="Y"): completed
  Result: <最终文本>
  </task-notification>
  ```

### 后台任务工具

- **F20**：新增 4 个内置工具：
  - `TaskList`：无参，返回当前 manager 中所有非 Terminated 任务的简要列表(id、name、status、tool_count、last_activity)
  - `TaskGet`：`{task_id}`，返回指定任务的完整状态(含 result / err)
  - `TaskStop`：`{task_id}`，调 `manager.stop` 触发取消；返回 `{status:"cancellation_requested"}`
  - `SendMessage`：`{name, message}`，按 name 找到仍存活的后台 Agent(status=completed, conv 仍在内存)，把 message 作为新 user 消息追加到 conv 并重新 `launch` 一轮跑动；找不到 / 已 cancelled 返回错误
- **F21**：本期不实现 `TaskCreate`；Hook 的 subagent 动作保持占位 stub（ch12 现状）

### Fork 路径

- **F22**：`build_forked_messages(parent_msgs, task)` 做三件事：
  1. 深拷贝 parent_msgs 的全部消息
  2. 把末尾 assistant 中未完成的 `tool_calls`（无对应 tool_results 回合）包装为 placeholder ToolResult（每条 `[forked, skipped]`），使消息格式合法
  3. 在末尾追加一条 user 消息，内容 = `FORK_BOILERPLATE`（纯 Boilerplate；任务文本由 `run_to_completion` 统一 `add_user(task)` 注入，避免重复）
- **F23**：Fork Boilerplate 是一段 `<fork_boilerplate>` 包裹的指令，核心约束：
  - 不能再 Fork（调用 Agent 工具会被拦截）
  - 不要对话 / 提问 / 请求确认
  - 直接使用工具
  - 严格限制在分配的任务范围内
  - 最终报告以 `Scope:` 开头，500 字以内
- **F24**：嵌套阻断**统一由工具过滤承担**（Tool 协议无 context，无法运行时检测调用方来源）：
  1. 定义式子 Agent：工具过滤剔除 `ALL_AGENT_DISALLOWED_TOOLS`（含 Agent），LLM 看不到 Agent 工具
  2. Fork 子 Agent：工具过滤同样剔除 Agent 工具（与定义式一致）——Fork 路径不再保留 Agent 工具
  3. `is_fork_context(msgs)`（扫描 `<fork_boilerplate>`）保留为辅助函数，供 `build_forked_messages` 测试与 debug，不作运行时拦截闸
- **F25**：定义式子 Agent 从空白对话启动（不注入 Boilerplate）

### 工具过滤多层防线

- **F26**：全局禁止列表 `ALL_AGENT_DISALLOWED_TOOLS = ["Agent", "TaskList", "TaskGet", "TaskStop", "SendMessage"]`——任何子 Agent 的工具过滤都剔除这些元工具（子 Agent 不能嵌套、不能操控后台任务）
- **F27**：自定义 Agent 额外限制 `CUSTOM_AGENT_DISALLOWED_TOOLS`：本期为空，接口预留
- **F28**：后台 Agent 白名单 `ASYNC_AGENT_ALLOWED_TOOLS`，只列基础工具：
  `read_file, write_file, edit_file, glob, grep, bash, load_skill, install_skill`
  MCP 工具按 `mcp__` 前缀动态识别放行。Fork / `run_in_background` 任意一种成立的子 Agent 工具集再叠加此白名单交集。
- **F29**：Agent 定义层 `tools`(白名单) 与 `disallowed_tools`(黑名单) 组合应用——白名单先确定范围，黑名单再排除
- **F30**：工具过滤合并执行顺序（子 Agent 构造时）：
  1. 起点 = registry 的全部工具
  2. 去掉 `ALL_AGENT_DISALLOWED_TOOLS`
  3. 如果是后台 → 取交集 `ASYNC_AGENT_ALLOWED_TOOLS (+ mcp__ 前缀)`
  4. 应用定义的 `disallowed_tools` 黑名单
  5. 应用定义的 `tools` 白名单（空白名单 = 不再收窄）
  6. 注入到子 Agent 的 `Agent(allowed_tools=allowed)`
- **F31**：工具列表对模型稳定——以上过滤只发生在子 Agent 构造时，主 Agent 看到的工具列表不变

### 内置角色与 Skill fork 改造

- **F32**：内置 3 个角色文件，随包发布：
  - `general-purpose.md`：无 disallowedTools，`inherit` 模型，maxTurns=30，permissionMode=default
  - `explore.md`：disallowedTools=[write_file, edit_file]，maxTurns=30，permissionMode=default（`model` 字段本期解析但不切 provider，故不写 haiku）
  - `plan.md`：disallowedTools=[Agent, write_file, edit_file]，maxTurns=15，permissionMode=plan
- **F33**：Skill fork 改造——`skills/executor.py::Executor._run_subagent` 改为调 `agent.launch_fork(opts)` 公共启动函数：
  1. 构造 `ForkLaunchOpts`（allowed_tools=skill.meta.allowed_tools、provider/model 切换逻辑保留、conv 由 skills 装填）
  2. 复用 `run_to_completion` 与工具过滤、消息装填路径
  3. 返回 `final_text` 行为不变（`ctx.conv.add_assistant(final)` 仍由 Executor 调）

## 非功能需求

- **N1**：工具列表稳定——主 Agent 看到的工具集不因 `.novacode/agents/` 增减或 Agent 工具被调用而变化（防止 prompt cache 抖动）
- **N2**：Fork 路径首次请求命中 prompt cache——`build_forked_messages` 拼接的消息列表前缀与父对话末尾完全一致，系统提示一致
- **N3**：子 Agent 崩溃不影响主程序——`manager.launch` 的协程包 `try/except BaseException`，任何异常转 `status=failed` + 错误信息回灌
- **N4**：启动期 fail-fast——内置定义解析失败立刻 raise（代码 bug）；用户/项目级定义文件解析失败仅 stderr 警告并跳过
- **N5**：与现有 ch11 Skill 系统、ch12 Hook 系统、ch08 权限系统、ch04 主 Agent loop 协同，不破坏既有测试
- **N6**：配置 `enable_subagent_background`(bool，默认 true，YAML key `enableSubAgentBackground`) 关闭后，Agent 工具的 `run_in_background:true` / 超时切后台全部失效，所有 SubAgent 强制前台同步；Fork 路径在此模式下报错「后台禁用，无法 Fork」
- **N7**：`<task-notification>` 注入主对话不消耗主 Agent 的工具调用配额，不出现在用户视窗（只对模型可见）

## 不做的事

- Worktree 文件隔离（独立章节）
- 多 Agent 团队编排（CrewAI / AutoGen 平等协作风格）
- 后台任务跨会话持久化——主程序退出后任务全部丢失
- 真正的插件系统（`SourcePlugin` 占位）
- 子 Agent 输出 schema 强制结构化（返回纯文本即可）
- Verification Agent 内置开关
- `TaskCreate` 工具（本期仅 List/Get/Stop/SendMessage）
- 跨 SubAgent token 用量汇总到 /status（只在 Manager 内部记录）
- 子 Agent Ask 升级主 TUI 弹窗（默认拒绝回灌）
- ESC 手动切后台
- `model` 字段切换 provider（单 provider 环境，恒用父 provider）

## 验收标准

- **AC1**：Agent 工具注册成功，主 Agent 的工具列表里 schema 一致（`prompt/description/subagent_type/model/run_in_background/name`）；子 Agent 看不到 Agent / TaskList / TaskGet / TaskStop / SendMessage
- **AC2**：`Agent` 工具调用 `{prompt:"...",subagent_type:"Explore"}` 时，主 Agent 看到的 tool_result 是 Explore 子 Agent 的最后一条 assistant 文本
- **AC3**：`Agent` 工具调用 `{prompt:"...",subagent_type:"non-existent"}` 时，主 Agent 看到的 tool_result 是结构化错误「未知 subagent_type」
- **AC4**：`Agent` 工具调用不传 subagent_type 时，子 Agent 收到的首条 user 消息以 `<fork_boilerplate>` 起头，且消息列表前缀与父对话一致（可由测试断言）
- **AC5**：Fork 子 Agent 的工具列表**不含** Agent 工具（与定义式一致，`ALL_AGENT_DISALLOWED_TOOLS` 统一剔除）
- **AC6**：定义式子 Agent 的工具列表里没有 Agent 工具
- **AC7**：子 Agent 角色 frontmatter 写 `permissionMode: dontAsk`，bash 等需要 Ask 的工具直接放行，无审批弹窗
- **AC8**：子 Agent 角色 frontmatter 不写 dontAsk，bash 触发 Ask → 经 `approval_upgrader` 默认拒绝 → tool_result 显示拒绝原因，子 Agent 调整行为（可观测：子 Agent 换工具/说明无法执行），主 TUI 不弹窗
- **AC9**：`run_in_background:true` 时 tool_result 立即返回 `{task_id, status:"async_launched"}`，主 Agent 不阻塞
- **AC10**：前台子 Agent 跑超过 `AUTO_BACKGROUND_SECONDS`（测试时调小），自动切后台，主 Agent 看到 tool_result 含 `status:"timed_out_to_background"`
- **AC11**：后台子 Agent 跑完，主 Agent 下次 run 的 reminder 区出现 `<task-notification>` 块，含 Result
- **AC12**：`TaskList` 工具返回当前后台任务列表，字段含 id/name/status/tool_count
- **AC13**：`TaskGet({task_id})` 返回 Result；`TaskStop({task_id})` 触发取消，任务 status 变 cancelled
- **AC14**：`SendMessage({name,message})` 让一个仍存活的后台 Agent 接到新任务并重新跑动，跑完结果作为新 `<task-notification>` 注入主对话
- **AC15**：项目级 `.novacode/agents/explore.md` 覆盖内置 `explore`，`resolve("explore")` 返回项目级版本
- **AC16**：Skill fork 模式调用走 SubAgent 底座——`skills/executor.py::Executor._run_subagent` 内部调 `agent.launch_fork(...)`
- **AC17**：N6 配置开关 `enable_subagent_background:false` 时，Fork 路径调用 Agent 工具返回结构化错误「后台禁用」
- **AC18**：子 Agent 异常 → status=failed，主 Agent 收到 `<task-notification>` 含错误描述，主程序不崩
- **AC19**：全新项目级自定义 Agent(`.novacode/agents/<name>.md`) 被 Catalog 加载；`subagent_type=<name>` 调用时，frontmatter 的 disallowedTools / permissionMode / maxTurns / SystemPrompt 全部生效——子 Agent 看不到黑名单工具、按指定 mode 决策、不超 turns、按 SystemPrompt 行事
- **AC20**：Agent 定义 frontmatter 的非法字段(unknown model / unknown permissionMode)在加载时 stderr 警告并 fallback 到默认值(model→inherit, mode→default)，novacode 不阻断启动，该 Agent 仍可被 resolve 与调用
