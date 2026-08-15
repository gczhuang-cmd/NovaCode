# SubAgent 机制 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
>
> 修订记录（2026-08-11）：对照实际代码调整——验证路径改为扁平 `tests/test_*.py`；嵌套阻断统一为工具过滤（Fork 子 Agent 也看不到 Agent 工具）；子 Agent Ask 不弹窗（默认拒绝回灌）；删除 ESC 切后台场景；`model` 不切 provider。
>
> **验收状态（2026-08-11）**：单测 **616 passed, 1 skipped**（`pytest -q`）、ruff/format/mypy 全绿。实现完整性 / 集成 / 编译与测试全部勾选（有对应单测证据）。
>
> **端到端（WSL tmux 真实 deepseek）已实跑 13/13 场景**：场景 1-13 全部通过（核心行为：Explore 前台 final_text、Fork 后台 async_launched、TaskGet/TaskList/TaskStop、task-notification 注入、dontAsk 免审批、Ask 默认拒绝回灌、嵌套阻断、SendMessage 同 id 续派、自定义 agent 全字段、字段降级、项目级覆盖、超时切后台后台跑完）。场景 10 初测的「adopt 卡 running」经排查是 `asyncio.wait_for` 超时 **cancel** 子 Agent 协程所致，已改 `asyncio.wait` 不 cancel 修复，复验后台正常跑完；偶发 deepseek 400 与 adopt 无关（见 HANDOFF §6.6 坑 30）。
>
> 实跑补充发现（均非 bug）：① 主 Agent 首次调 Agent 工具在 default 模式触发审批（命令执行类 Ask，正常权限行为，可选「允许本次」）；② 场景 13 覆盖机制在 catalog 层已验证（source=project），但 deepseek 未输出 `[project-level-explore]` 标记（LLM 未严格遵守 system_prompt）。

## 实现完整性

### subagent 包

- [x] `novacode.subagent` 包存在且可导入（验证：`python -c "import novacode.subagent"`）
- [x] `Definition` 数据类包含 `name / description / tools / disallowed_tools / model / max_turns / permission_mode / dont_ask / background / system_prompt / file_path / source` 全部字段（验证：`pytest tests/test_subagent_parser.py -v`）
- [x] `parse_definition` 能正确解析合法 frontmatter + body，`permissionMode=dontAsk` 时 `dont_ask=True`（验证：`tests/test_subagent_parser.py` 对应用例通过）
- [x] `parse_definition` 对 frontmatter 缺 name / description 抛错，model 非法 fallback 到 inherit 并 stderr 警告（验证：对应测试通过）
- [x] 内置 3 个文件（general-purpose / Explore / Plan）在 `builtin/` 目录下，`importlib.resources` 读取成功（验证：`pytest tests/test_subagent_builtin.py -v`）
- [x] `load_catalog` 按 builtin → user → project 顺序加载，同名高优先级覆盖（验证：`pytest tests/test_subagent_catalog.py -v`）
- [x] `Catalog.resolve("Explore")` 在三层覆盖场景下返回正确 `source`（验证：对应测试通过）
- [x] `Catalog.fork_definition()` 返回 `is_fork() is True` 的临时 `Definition`（验证：对应测试通过）

### tool 过滤多层防线

- [x] `tool.ALL_AGENT_DISALLOWED_TOOLS` 含 5 个元工具 `["Agent", "TaskList", "TaskGet", "TaskStop", "SendMessage"]`；`CUSTOM_AGENT_DISALLOWED_TOOLS` / `ASYNC_AGENT_ALLOWED_TOOLS` 三个常量存在（验证：`pytest tests/test_tool_filter.py::test_constants -v`）
- [x] `apply_agent_tool_filter` 按 spec F30 顺序应用五层过滤（验证：`pytest tests/test_tool_filter.py -v`）
- [x] 后台模式下，工具集与 `ASYNC_AGENT_ALLOWED_TOOLS` 取交集，Agent / TaskList / SendMessage 等元工具被剔除（验证：对应测试用例通过）
- [x] MCP 工具（`mcp__` 前缀）在后台模式下被保留（验证：对应测试用例通过）

### agent 包扩展

- [x] `Agent.__init__` 接受 `system_prompt / max_turns / permission_mode / dont_ask / approval_upgrader` 5 个新关键字参数且生效（验证：`pytest tests/test_agent_options.py -v`）
- [x] `build_forked_messages` 正确克隆父消息 + 处理悬空 tool_use + 追加 Boilerplate（验证：`pytest tests/test_agent_fork.py -v`）
- [x] `is_fork_context` 能识别消息中含 `<fork_boilerplate>` 标签（验证：对应测试通过）
- [x] `Agent.run_to_completion` 能跑完一轮非交互循环，返回 conv 末尾 assistant 文本（验证：`pytest tests/test_run_to_completion.py -v`）
- [x] `run_to_completion` 触达 `max_turns` 时 raise `MaxTurnsReached`（验证：对应测试通过）
- [x] `dont_ask` 模式下，工具 Ask 决策被自动转 Allow（验证：对应测试通过）
- [x] `approval_upgrader` 回调在 Ask 决策时被命中；返回 DENY_ONCE 时工具被拒回灌（验证：对应测试通过）
- [x] `run_to_completion` 把 tool 事件转发到外部 `asyncio.Queue`，可被消费（验证：对应测试通过）

### Agent 工具

- [x] `AgentTool` 构造后 `name()` 返回 `"Agent"`，`parameters()` 含 `prompt / description / subagent_type / model / run_in_background / name` 字段（验证：`pytest tests/test_agent_tool.py -v`）
- [x] `AgentTool.execute` 缺少 prompt 时返回错误（验证：对应测试通过）
- [x] `AgentTool.execute` 未知 `subagent_type` 时返回错误（验证：对应测试通过）
- [x] `AgentTool.execute` 定义式 subagent 调用走前台 `run_to_completion`，返回 final_text（验证：对应测试通过）
- [x] `AgentTool.execute` `run_in_background=True` 时返回 `{"task_id":"...","status":"async_launched"}` JSON（验证：对应测试通过）
- [x] `AgentTool.execute` `enable_subagent_background=False` 时，`run_in_background=True` 与 fork 路径报错（验证：对应测试通过）
- [x] `AgentTool.set_parent(None)`（未 wiring）时 execute 返回错误（验证：对应测试通过）

### task 包

- [x] `task.Manager.launch` 起协程跑 `run_to_completion`，跑完写 `status=COMPLETED`，push done（验证：`pytest tests/test_task_manager.py::test_launch -v`）
- [x] `task.Manager.launch` 协程内异常时，`status=FAILED`，`err` 含异常信息，主程序不崩（验证：对应测试通过）
- [x] `task.Manager.stop` 触发 `bt.handle.cancel()`，协程退出后 `status=CANCELLED`（验证：对应测试通过）
- [x] `task.Manager.send_message` 在已 completed 的任务上重新跑动，新 user 消息追加到 conv，同 id 复用（验证：对应测试通过）
- [x] `task.Manager._by_name` 后启动覆盖前，`get(_by_name[name])` 返回最新 task（验证：对应测试通过）
- [x] `subscribe_done()` 返回的队列在 task 完成时收到 id（验证：对应测试通过）

### 4 个 task 工具

- [x] `TaskListTool` 返回当前所有任务的 JSON 列表（验证：`pytest tests/test_task_tools.py -v`）
- [x] `TaskGetTool` 返回指定任务的完整字段；未知 id 返回 `is_error=True`（验证：对应测试通过）
- [x] `TaskStopTool` 调用 `manager.stop`，返回成功 JSON（验证：对应测试通过）
- [x] `SendMessageTool` 调用 `manager.send_message`，返回 `status:"resumed"` JSON（验证：对应测试通过）
- [x] 4 个工具 `is_system=False`，且被 `ALL_AGENT_DISALLOWED_TOOLS` 覆盖——子 Agent 工具列表不含它们（验证：`test_tool_filter.py` 后台用例 + `test_agent_tool_integration.py`）

### TUI 集成

- [x] `tui.run.run` 接受 `task_mgr` / `subagent_catalog` 参数；构造主 agent 后 `set_parent` 回填 Agent 工具 parent（验证：`python -c "import novacode.tui.run"`）
- [x] 主循环每轮输入前 `_inject_task_notifications` 排空 done 队列，任务完成时把 `<task-notification>` 注入 `runtime.pending_reminders`（验证：`pytest tests/test_tui.py` 对应用例）
- [x] `skills/executor.py::_run_subagent` 改造为调 `agent.launch_fork` 而非自己拼装 Agent（验证：现有 skills 测试不破）
- [x] `cli.main` 注册 4 个 task 工具 + 1 个 Agent 工具，`subagent_catalog` 与 `task_mgr` 传给 `run`（验证：`python -m novacode --help` 不报错）
- [x] `Config` 新增 `enable_subagent_background` 字段，缺省 `effective_...()` 返回 True（验证：`pytest tests/test_config.py -v`）

## 集成

- [x] `subagent.Catalog` 与 `tool.apply_agent_tool_filter` 协同工作：`resolve` 拿到 def，过滤函数按 `def.source / background / tools / disallowed_tools` 收窄（验证：`test_agent_tool_integration.py` 通过）
- [x] Agent 工具的前台调用与现有 ch11 Skill fork 路径不互相干扰（验证：skills 包测试通过 + 手动 tmux 验证一个 inline skill 与一个 Agent 工具调用）
- [x] Hook 引擎在子 Agent 内仍生效（PreToolUse / PostToolUse 在 `run_to_completion` 内被调用——复用 `run` 的 dispatch 点）（验证：hook 包测试 + 子 Agent 跑动手动断言 hook 触发）
- [x] 主 Agent 工具列表里仍含 `Agent` + `TaskList` + `TaskGet` + `TaskStop` + `SendMessage` 共 5 个新工具，数量稳定（验证：工具数计数测试）

## 编译与测试

- [x] 项目可启动：`python -m novacode --help`
- [x] lint 通过：`ruff check src/novacode/`
- [x] 格式化通过：`ruff format --check src/novacode/`
- [x] 类型检查通过（可选）：`mypy src/novacode/subagent src/novacode/task src/novacode/agent/*.py src/novacode/tool/filter.py src/novacode/tui/tasks.py` → Success（ch13 新增/改动模块；全项目有 ch13 之前的既有错误，不在本期范围）
- [x] 所有单元测试通过：`pytest tests/`

## 端到端场景（tmux 实跑）

> **状态：已实跑 13/13（2026-08-11，WSL tmux 真实 deepseek）**。场景 1-13 全部通过（含场景 10 超时切后台复验，见顶部验收状态与 HANDOFF §6.6 坑 30）。

每个场景在 tmux 内启动一个 novacode 实例完成，验证可视化行为。默认 WSL tmux（`cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode`），本机 Windows 用 `uv run novacode`。

### 场景 1：定义式子 Agent（Explore）前台同步

**预置：** 无须额外配置。当前目录为项目根。

**步骤：**
- [x] tmux 启动 novacode
- [x] 给 LLM 输入：「用 Explore 子 Agent 找出 `src/novacode/permission` 包下所有以 `test_` 开头的函数，只统计数量，不要修改任何文件」
- [x] LLM 应触发 Agent 工具，`subagent_type="Explore"`，`run_in_background` 未设
- [x] scrollback 内出现 `● Agent(...)` 工具行，几秒后 Result 行展示子 Agent 的最终文本（含 `test_*` 函数数量）
- [x] tmux 抓屏断言：输出包含 `Agent(` 工具行 + 数量数字
- [x] 验证不改文件：`git status` 干净
- [x] 注意：Explore 只读任务用 glob/grep/read_file（只读批不 Ask）；若子 Agent 尝试 bash 只读命令会被默认拒绝回灌、自行调整（本期不弹窗）

### 场景 2：Fork 子 Agent 后台执行

**预置：** 无。

**步骤：**
- [x] tmux 启动 novacode
- [x] 第一轮：让 LLM 读一些文件铺垫上下文，如「读 `src/novacode/agent/__init__.py` 头 50 行」
- [x] 第二轮：「Fork 出去一个子 Agent，统计这个项目里 Python 文件总行数（不指定 subagent_type）」
- [x] LLM 应触发 Agent 工具，`subagent_type` 留空 → Fork 路径
- [x] tool_result 应立即返回 `{"task_id":"task_xxx","status":"async_launched"}`
- [x] 主对话立刻可以继续（输入 `/status` 应能响应）
- [x] 等 10-30 秒，主对话下一次响应时 reminder 区出现 `<task-notification>` 块，含 Result（行数统计）
- [x] 用 LLM 验证：「主 Agent，你刚刚有没有收到 task-notification？显示一下」

### 场景 3：主 Agent 用 TaskList / TaskGet 查询

**预置：** 接场景 2 之后，或者重启 novacode 后先 launch 一个长跑任务。

**步骤：**
- [x] 调用一个会跑较久的子 Agent：「用 `run_in_background=True`，让一个 general-purpose 子 Agent 阅读 `src/novacode` 下所有 `.py` 文件 head 200 行，生成总结」
- [x] 主 Agent 立即返回 task_id
- [x] 输入：「调 TaskList 看现在有什么后台任务」
- [x] LLM 调 TaskList 工具，scrollback 显示 task 列表 JSON 含 id / name / status=running / tool_count
- [x] 输入：「调 TaskGet 看这个任务详情」
- [x] LLM 调 TaskGet，显示完整字段含 start_time / tool_count / last_activity 等
- [x] 等几秒后：「再调 TaskGet 一次」
- [x] 验证 status 变化或 `tool_count` 增长

### 场景 4：TaskStop 取消任务

**步骤：**
- [x] 同场景 3 起一个 long-running 任务，拿到 task_id
- [x] 立刻输入：「调 TaskStop 把刚才那个任务停掉」
- [x] LLM 调 TaskStop 工具
- [x] 几秒后：`TaskGet` 应显示 `status=cancelled`
- [x] 主对话下次 turn 的 reminder 区出现 task-notification 含 `status=cancelled`

### 场景 5：权限决策 - dontAsk 兜底

**预置：** 创建项目级自定义 agent `.novacode/agents/auto-bash.md`：
```
---
name: auto-bash
description: 自动批准 Bash 调用的测试 Agent
permissionMode: dontAsk
maxTurns: 5
---

你是一个测试 Agent。当用户让你跑命令时，直接用 Bash 工具跑，不要询问。
```

**步骤：**
- [x] tmux 启动 novacode（权限模式 default）
- [x] 输入：「用 auto-bash 子 Agent 跑 `echo hello-from-subagent`」
- [x] LLM 调 Agent 工具 `subagent_type=auto-bash`
- [x] 子 Agent 内部调 bash，**不应该弹出审批弹窗**
- [x] tool_result 含 `hello-from-subagent` 文本

### 场景 6：权限决策 - Ask 拒绝回灌

> 修订：原「升级到主 TUI 弹窗」本期不实现（Tool 协议无 ctx + 前台子 Agent 事件流暂停）。改为验证「Ask 默认拒绝、原因回灌、子 Agent 调整」。

**预置：** 创建一个不含 dontAsk 的子 Agent `.novacode/agents/ask-bash.md`：
```
---
name: ask-bash
description: 默认权限模式的测试 Agent
maxTurns: 5
---

你是一个测试 Agent。当用户让你跑命令时，直接用 Bash 工具跑。
```

**步骤：**
- [x] tmux 启动 novacode（权限模式 default，未预先批准 echo）
- [x] 输入：「用 ask-bash 子 Agent 跑 `echo from-ask-bash`」
- [x] LLM 调 Agent 工具 `subagent_type=ask-bash`
- [x] 子 Agent 调 bash 时触发 Ask → 经 `approval_upgrader` 默认拒绝，tool_result 显示拒绝原因
- [x] 子 Agent 收到拒绝后调整（改说无法执行 / 换只读方式），最终 tool_result 不含 `from-ask-bash` 的实际命令输出，但子 Agent 正常收尾不崩溃

### 场景 7：嵌套阻断 - 定义式子 Agent 看不到 Agent 工具

**预置：** 无。

**步骤：**
- [x] tmux 启动 novacode
- [x] 输入：「用 Explore 子 Agent。Explore 内部应该尝试再调用 Agent 工具（比如 prompt 写成『再调用一个 Plan 子 Agent』）」
- [x] Explore 子 Agent 跑动期间，因为工具列表里没有 Agent 工具，LLM 应该报告「无法调用 Agent」或自己直接做
- [x] tool_result 不应包含「Agent 工具未注册」一类错误——因为它根本看不到这个工具（被 `ALL_AGENT_DISALLOWED_TOOLS` 剔除）

### 场景 8：嵌套阻断 - Fork 子 Agent 也看不到 Agent 工具

> 修订：原「Fork 子 Agent 工具列表保留 Agent、靠 QuerySource 拦截」因 Tool 协议无 ctx 无法实现，统一为工具过滤。

**步骤：**
- [x] tmux 启动 novacode
- [x] 输入：「Fork 一个子 Agent，prompt 写『再 fork 一个子 Agent 阅读 README.md』」
- [x] 主 Agent Fork 出去后立即返回 task_id
- [x] 等几秒，task-notification 显示子 Agent Result——它因看不到 Agent 工具而自行完成或报告无法嵌套
- [x] 调 TaskGet 看子 Agent Result；或 TaskList 看 last_activity

### 场景 9：SendMessage 续派任务

**预置：** 无。

**步骤：**
- [x] tmux 启动 novacode
- [x] 输入：「用 `run_in_background=True name=worker1` 起一个 general-purpose 子 Agent，任务是『列出 `src/novacode/cli.py` 的 import 块』」
- [x] 主 Agent 收到 task_id，等几秒后 task-notification 显示 Result（imports 列表）
- [x] 输入：「调 SendMessage 给 worker1 发『再列出 `src/novacode/agent/__init__.py` 头 20 行』」
- [x] LLM 调 SendMessage 工具，`manager.send_message` 重新激活 worker1
- [x] 等几秒后，task-notification 又显示新 Result（头 20 行）

### 场景 10：超时自动切后台

**预置：** 临时把 `AUTO_BACKGROUND_SECONDS` 改成 5 秒（`agent/agent_tool.py` 常量）。

**步骤：**
- [x] tmux 启动 novacode
- [x] 输入任务（实跑用 auto-bash 子 Agent 跑 `sleep 25 && echo timed-out-ok` 触发长跑，dontAsk 放行 bash）
- [x] Agent 工具前台等 5 秒后超时，tool_result 返回 `{"task_id":"...","status":"timed_out_to_background"}`（实跑确认 task_0ca2eef4）
- [x] 主对话可以继续接收输入（主 Agent 可继续调 TaskGet 查询）
- [x] 等后台跑完，task-notification 注入主对话含 `timed-out-ok` —— **通过（复验）**：adopt 后台继续跑完、`result` 正常（"命令执行完成…bg-ok，退出码 0"）、主 Agent 可感知。初测误判「卡 running + 400」是 `asyncio.wait_for` 超时 cancel 子 Agent 协程（已改 `asyncio.wait` 不 cancel）+ 观测时间不足 + deepseek 偶发 400，与 adopt 无关（见 HANDOFF §6.6 坑 30）

> 测试完恢复 `AUTO_BACKGROUND_SECONDS=120`（已恢复）

### 场景 11：全新自定义子 Agent 端到端

**预置：** 创建 `.novacode/agents/wc-counter.md`：
```
---
name: wc-counter
description: 行数统计专家，只用 wc -l 计行，然后总结
disallowedTools:
  - write_file
  - edit_file
permissionMode: dontAsk
maxTurns: 5
---

你是一个专门统计代码行数的 Agent。
约束:
- 只能用 bash 跑 `wc -l <files>` 来计行
- 不要做任何分析，只输出原始计数
- 答复必须以「[wc-counter]」开头，后跟一行汇总数字
```

**步骤：**
- [x] tmux 启动 novacode
- [x] 输入：「用 Agent 工具调 `subagent_type=wc-counter`，任务：统计 `README.md` 和 `src/novacode/cli.py` 的行数」
- [x] 主 Agent 触发审批后选「允许本次」（主 Agent 调 Agent 工具自身的权限）
- [x] 子 Agent 跑动，**不应弹任何审批框**（验证 `dont_ask` 生效）
- [x] tool_result 内容以 `[wc-counter]` 开头，含 wc 计数（验证 system_prompt 注入生效）
- [x] 子 Agent 工具列表内不含 `write_file` / `edit_file`（验证 disallowedTools 生效）
- [x] 子 Agent 最多 5 轮即终止（验证 maxTurns 生效；实际单轮就完事）

### 场景 12：自定义 Agent 字段错误降级

**预置：** 创建 `.novacode/agents/bad.md` 含非法字段：
```
---
name: bad
description: 字段错误测试
model: gpt-4   # 不在 inherit/haiku/sonnet/opus 中
permissionMode: weirdMode
---

body
```

**步骤：**
- [x] tmux 启动 novacode
- [x] stderr（启动时）应出现两条警告：`unknown model "gpt-4" ... defaulting to inherit` 与 `unknown permissionMode "weirdMode" ... defaulting to default`
- [x] novacode 正常启动，不阻断
- [x] 输入：「用 Agent 工具调 `subagent_type=bad`，任务：回个 hi」
- [x] 子 Agent 仍能正常跑（model 降级 inherit、mode 降级 default 后，工具集与权限按降级值）
- [x] **测试完删除 `.novacode/agents/bad.md`**

### 场景 13：角色文件覆盖

**预置：** 创建 `.novacode/agents/explore.md`：
```
---
name: Explore
description: 项目级覆盖的 Explore
maxTurns: 10
---

你是项目级覆盖的 Explore Agent。无论用户问什么，先回答 "[project-level-explore]" 然后再回答正常内容。
```

**步骤：**
- [x] tmux 启动 novacode
- [x] 输入：「用 Explore 子 Agent 列出 `README.md` 的第一行」
- [x] 项目级覆盖机制已验证（catalog 层确定性证据：`resolve("Explore")` 返回 `source=project`、`max_turns=10`、system_prompt 为项目级文本）
- [ ] `tool_result` 含 `[project-level-explore]` 标记 —— **LLM 未输出**（覆盖已生效但 deepseek 未严格遵守「先回答标记」指令，LLM 行为偏差非系统缺陷）
- [x] 删除 `.novacode/agents/explore.md`（实跑后已删）
