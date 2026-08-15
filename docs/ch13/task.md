# SubAgent 机制 Tasks

> 修订记录（2026-08-11，对照实际代码）：
> - **文件结构对齐**：agent 包是 `agent/__init__.py`（非 agent.py）；Skill fork 在 `skills/executor.py`（非 `tui/skill_fork.py`）；`launch_fork` 放 `agent/launch.py`；TUI 改动在 `tui/run.py`（非 `tui/app.py`/`stream.py`）。
> - **tests 扁平化**：`tests/subagent/`、`tests/task/` 子目录改为 `tests/test_subagent_*.py`、`tests/test_task_*.py`（对齐现有布局）。
> - **Tool 协议**：`execute(args: str) -> Result`（raw JSON 字符串，无 ToolContext）；`name()`/`description()`/`parameters()` 是方法，`read_only`/`is_system` 是类属性。
> - **类型命名**：`Mode`/`Outcome`/`Decision`/`parse_mode`；`ApprovalRequest` 在 `agent/__init__.py`。
> - **`SessionRuntime` 是 dataclass**：所有 `SessionRuntime(200_000)` 改为完整关键字构造。
> - **删除**：ESC 切后台任务（T27）、子 Agent Ask 弹窗任务（改为默认拒绝）、model 切 provider 逻辑。
> - **嵌套阻断**：统一 `ALL_AGENT_DISALLOWED_TOOLS` 工具过滤，`is_fork_context` 不作运行时闸。
> - **`ALL_AGENT_DISALLOWED_TOOLS` 含 5 元工具**：Agent + TaskList/TaskGet/TaskStop/SendMessage。

> 包名：`novacode`(Python 3.12+)。源码位于 `src/novacode/`，内部模块以 `novacode.xxx` 导入。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/novacode/subagent/__init__.py` | 包公共导出 |
| 新建 | `src/novacode/subagent/definition.py` | `Definition` / `Source` 类型 |
| 新建 | `src/novacode/subagent/parser.py` | `parse_definition` / `parse_file` |
| 新建 | `tests/test_subagent_parser.py` | 解析与字段校验单测 |
| 新建 | `src/novacode/subagent/catalog.py` | `Catalog` + `load_catalog` / `resolve` / `list` / `fork_definition` |
| 新建 | `tests/test_subagent_catalog.py` | 多来源加载与覆盖测试 |
| 新建 | `src/novacode/subagent/embed.py` | `builtin_definitions()` |
| 新建 | `src/novacode/subagent/builtin/general-purpose.md` | 内置 general-purpose 定义 |
| 新建 | `src/novacode/subagent/builtin/explore.md` | 内置 Explore 定义 |
| 新建 | `src/novacode/subagent/builtin/plan.md` | 内置 Plan 定义 |
| 新建 | `tests/test_subagent_builtin.py` | 内置定义加载测试 |
| 新建 | `src/novacode/tool/filter.py` | `ALL_AGENT_DISALLOWED` / `ASYNC_AGENT_ALLOWED` / `apply_agent_tool_filter` |
| 新建 | `tests/test_tool_filter.py` | 过滤多层防线测试 |
| 新建 | `src/novacode/agent/permission_upgrade.py` | `ApprovalUpgrader` 类型 + `default_upgrader` |
| 新建 | `src/novacode/agent/fork.py` | `build_forked_messages` + `is_fork_context` + `FORK_BOILERPLATE` |
| 新建 | `tests/test_agent_fork.py` | Fork 消息构造与上下文识别测试 |
| 新建 | `src/novacode/agent/run_to_completion.py` | `run_to_completion`（挂到 `Agent` 上） |
| 新建 | `tests/test_run_to_completion.py` | `run_to_completion` / `dont_ask` / `max_turns` / `approval_upgrader` 测试 |
| 新建 | `tests/test_agent_options.py` | Agent 新构造参数测试 |
| 新建 | `src/novacode/agent/agent_tool.py` | `AgentTool` + `AgentArgs` |
| 新建 | `tests/test_agent_tool.py` | Agent 工具调用、后台、超时切后台测试 |
| 新建 | `src/novacode/agent/launch.py` | `ForkLaunchOpts` + `launch_fork` |
| 新建 | `src/novacode/task/__init__.py` | 包公共导出 |
| 新建 | `src/novacode/task/manager.py` | `Manager` + `BackgroundTask` + `PartialState` + `Usage` |
| 新建 | `tests/test_task_manager.py` | 后台任务全生命周期测试 |
| 新建 | `src/novacode/task/tools.py` | `TaskListTool` / `TaskGetTool` / `TaskStopTool` / `SendMessageTool` |
| 新建 | `tests/test_task_tools.py` | 4 个工具的单测 |
| 新建 | `src/novacode/tui/tasks.py` | `build_task_notification` + `_inject_task_notifications` |
| 修改 | `src/novacode/agent/__init__.py` | 加 5 个新字段；`run` 循环上限改 `max_turns`；`_execute_batched` 串行分支加 `dont_ask` 短路 + `approval_upgrader` |
| 修改 | `tests/test_agent.py` | 不破坏既有测试 |
| 修改 | `src/novacode/tui/run.py` | 加 `task_mgr` / `subagent_catalog` 参数；agent 构造后 `set_parent`；每轮注入 task notification |
| 修改 | `tests/test_tui.py` | 补 task-notification 注入测试 |
| 修改 | `src/novacode/skills/executor.py` | `_run_subagent` 改造为调 `agent.launch_fork` |
| 修改 | `tests/test_skills_executor.py` | 不破坏既有测试 |
| 修改 | `src/novacode/config.py` | 加 `enable_subagent_background: bool \| None` |
| 修改 | `tests/test_config.py` | 加配置字段测试 |
| 修改 | `src/novacode/cli.py` | 装配 subagent_catalog / task_mgr / 5 工具注册 |
| 新建 | `tests/test_agent_tool_integration.py` | 完整路径集成测试 |

## T1: subagent 包的 Definition 与 Source 类型

**文件：** `src/novacode/subagent/definition.py`
**依赖：** 无
**步骤：**
1. 新建包 `novacode.subagent`，加 `__init__.py`（后续任务逐步填充导出）与 `definition.py`，声明 `Source(IntEnum)`：
   - `BUILTIN = 0` / `USER = 1` / `PROJECT = 2` / `PLUGIN = 3`（占位）
2. `Source.__str__` 返回 `"builtin" / "user" / "project" / "plugin"`，越界返回 `"unknown"`
3. 声明 `Definition` `@dataclass`，字段如 plan.md 所述：`name / description / tools / disallowed_tools / model / max_turns / permission_mode / dont_ask / background / system_prompt / file_path / source`
4. `permission_mode` 用 `novacode.permission.Mode` 类型，默认 `Mode.DEFAULT`
5. `Definition.is_fork()` 返回 `self.name == "__fork__"`
6. docstring 标注字段语义，引用 spec F4

**验证：** `python -c "from novacode.subagent.definition import Definition, Source"` 导入无误

## T2: subagent 解析器

**文件：** `src/novacode/subagent/parser.py`
**依赖：** T1
**步骤：**
1. 新建 `parser.py`，参照 `skills/parser.py::parse_frontmatter_and_body` 的思路独立实现一份（strip BOM / 归一化 CRLF / YAMLError 转 ValueError / `---` 未闭合抛 ValueError）
2. 声明 `AGENT_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9\-_]{0,31}$")`（大小写都允许，对齐 `Explore` / `Plan`）
3. 实现 `parse_definition(data: bytes, file_path: str, source: Source) -> Definition`：
   - 调 frontmatter/body 分离，YAML `safe_load` 成 dict
   - 字段映射：
     ```python
     name = str(fm.get("name", "")).strip()
     description = str(fm.get("description", "")).strip()
     tools = [str(x) for x in (fm.get("tools") or [])]
     disallowed_tools = [str(x) for x in (fm.get("disallowedTools") or [])]
     model_str = str(fm.get("model") or "").strip()
     max_turns = int(fm.get("maxTurns") or 0)
     permission_mode_str = str(fm.get("permissionMode") or "").strip()
     background = bool(fm.get("background") or False)
     ```
   - 校验 name 非空且匹配 `AGENT_NAME_REGEX`，否则抛 ValueError
   - 校验 description 非空，否则抛 ValueError
   - 校验 model：空 / `"inherit"` / `"haiku"` / `"sonnet"` / `"opus"` 之一；其它 stderr 警告并改为 `"inherit"`
   - 解析 permission_mode：`"dontAsk"` 单独识别 → `dont_ask=True`、`permission_mode=Mode.DEFAULT`；否则调 `parse_mode()`，失败 stderr 警告并降级 `Mode.DEFAULT`
   - 映射到 Definition：`system_prompt = body`、`file_path`、`source`
4. 实现 `parse_file(path: str, source: Source) -> Definition`：`Path(path).read_bytes()` + `parse_definition`

**验证：** `pytest tests/test_subagent_parser.py -v` 通过（对应 T3 的测试）

## T3: subagent 解析器测试

**文件：** `tests/test_subagent_parser.py`
**依赖：** T2
**步骤：**
1. `pytest.mark.parametrize` 覆盖：正常完整 frontmatter / 仅必填 / `permissionMode=dontAsk` → `dont_ask=True` / `model` 非法 → 警告 fallback / 缺 name 抛 ValueError / 缺 description 抛 ValueError / frontmatter 未关闭 → 抛错
2. body 区段提取：验证 `---` 后的内容（去 BOM 去前导换行）被完整取到 `system_prompt`
3. 用 `tmp_path` 写一个 `.md` 文件测 `parse_file`
4. 用 `capsys` 捕获 stderr 验证 fallback 时的警告输出

**验证：** `pytest tests/test_subagent_parser.py -v` 全部通过

## T4: 内置 Agent 定义文件

**文件：** `src/novacode/subagent/builtin/{general-purpose,explore,plan}.md`
**依赖：** 无
**步骤：**
1. 创建目录 `src/novacode/subagent/builtin/`
2. `general-purpose.md`：
   ```yaml
   ---
   name: general-purpose
   description: 通用子 Agent，拥有全部工具，用于需要完整能力但独立上下文的场景
   maxTurns: 30
   ---

   你是 novaCode 的通用 Agent。根据用户的消息，使用可用工具完成任务。
   把任务做完，不要过度设计，但也不要做一半就停。
   完成后用简洁的报告回复：做了什么、关键发现。
   调用方会把结果转述给用户，所以只需要包含要点。
   ```
3. `explore.md`（本期不写 model——不切 provider）：
   ```yaml
   ---
   name: Explore
   description: 只读代码探索 Agent，适合搜索、阅读、理清调用链；不能修改文件
   disallowedTools:
     - write_file
     - edit_file
   maxTurns: 30
   ---

   你是一个文件搜索专家。这是一个只读探索任务。
   严禁：创建文件、修改文件、删除文件、执行任何改变系统状态的命令。
   工具策略：Glob 做文件模式匹配、Grep 搜索文件内容、Read 读取已知路径、Bash 仅用于只读操作（ls、git log、find、cat）。
   尽可能并行发起多个工具调用。高效完成搜索请求，清晰报告发现。
   ```
4. `plan.md`：
   ```yaml
   ---
   name: Plan
   description: 计划 Agent，分析需求、制定执行计划，但不直接执行；主 Agent 拿到计划后逐步执行
   disallowedTools:
     - write_file
     - edit_file
     - Agent
   maxTurns: 15
   permissionMode: plan
   ---

   你是一个软件架构师和规划专家。这是一个只读规划任务。
   严禁：创建文件、修改文件、删除文件、执行任何改变系统状态的命令。
   工作流程：① 理解需求 ② 用搜索工具充分探索代码库 ③ 设计方案 ④ 输出分步实现计划。
   回复末尾必须列出 3-5 个对实现最关键的文件路径。
   ```
5. 确认 `pyproject.toml` 打包包含 `*.md`（hatch 默认包含同包目录下任意文件，通常无需额外配置）

**验证：** 三个 `.md` 存在，frontmatter 合法；`parse_file` 测试不报错

## T5: subagent embed 与内置加载

**文件：** `src/novacode/subagent/embed.py`
**依赖：** T2, T4
**步骤：**
1. 新建 `embed.py`，`from importlib.resources import files`
2. 实现 `builtin_definitions() -> list[Definition]`：
   - `pkg = files("novacode.subagent.builtin")`
   - 遍历 `pkg.iterdir()`，过滤 `name.endswith(".md")`
   - 对每个文件 `data = (pkg / name).read_bytes()` + `parse_definition(data, f"builtin:{name}", Source.BUILTIN)`
   - 解析失败 raise（内置代码 bug，启动期失败即灾难，N4）
3. 返回按 `name` 升序的 list

**验证：** `pytest tests/test_subagent_builtin.py -v` 通过（T7）

## T6: Catalog 与三层加载

**文件：** `src/novacode/subagent/catalog.py`
**依赖：** T1, T2, T5
**步骤：**
1. 新建 `catalog.py`，声明 `Catalog` 类（见 plan.md）
2. 实现 `load_catalog(root: str) -> Catalog`：
   ```python
   c = Catalog()
   c._add_all(builtin_definitions(), Source.BUILTIN)
   c._add_all(_load_from_dir(Path.home() / ".novacode" / "agents", Source.USER), Source.USER)
   c._add_all(_load_from_dir(Path(root) / ".novacode" / "agents", Source.PROJECT), Source.PROJECT)
   return c
   ```
   - `Path.home()` 封装独立函数 `_home()`（Windows 读 USERPROFILE，测试 monkeypatch 该函数，对齐 `skills/catalog.py` 的教训）
3. 实现 `_load_from_dir(dir: Path, source: Source) -> list[Definition]`：目录不存在 → `[]`；遍历 `dir.glob("*.md")` 逐个 `parse_file`，失败 stderr 警告并跳过
4. 实现 `Catalog._add_all(defs, source)`：同名时高优先级覆盖（builtin → user → project 顺序，后加者覆盖）；同时往 `self._by_source[source]` 追加
5. 实现 `resolve(name)` / `list()`（按 name 升序）/ `list_by_source(src)`
6. 实现 `fork_definition()`：
   ```python
   return Definition(
       name="__fork__",
       description="Fork-based subagent",
       model="inherit",
       permission_mode=Mode.DEFAULT,
   )
   ```

**验证：** `pytest tests/test_subagent_catalog.py -v` 通过

## T7: Catalog 测试

**文件：** `tests/test_subagent_catalog.py`、`tests/test_subagent_builtin.py`
**依赖：** T6
**步骤：**
1. `test_subagent_builtin.py`：`builtin_definitions` 返回 3 个 def（general-purpose / Explore / Plan）
2. 三层覆盖：`tmp_path` fixture 造项目 root 与用户路径（**monkeypatch `catalog._home`**，不是 `setenv("HOME")`），分别放 `explore.md`
3. 验证 `resolve("Explore")` 在三种情形下返回的 `source` 正确（都有 → project；只有 user+builtin → user；只有 builtin → builtin）
4. `fork_definition` 返回 `is_fork() is True`
5. 加载错误处理：放一个非法 frontmatter 文件，该文件*被跳过*，其他文件仍正常（`capsys` 验证 stderr 警告）

**验证：** `pytest tests/test_subagent_parser.py tests/test_subagent_catalog.py tests/test_subagent_builtin.py -v` 全部通过

## T8: 工具过滤多层防线

**文件：** `src/novacode/tool/filter.py`
**依赖：** 无
**步骤：**
1. 新建 `filter.py`，声明三个全局常量：
   ```python
   ALL_AGENT_DISALLOWED_TOOLS: list[str] = [
       "Agent", "TaskList", "TaskGet", "TaskStop", "SendMessage",
   ]
   CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = []
   ASYNC_AGENT_ALLOWED_TOOLS: list[str] = [
       "read_file", "write_file", "edit_file",
       "glob", "grep",
       "bash",
       "load_skill", "install_skill",
   ]
   ```
2. 声明 `FilterParams` `@dataclass`（`source` 注释：0=builtin,1=user,2=project,3=plugin，与 `subagent.Source` 对齐）
3. 实现 `apply_agent_tool_filter(p: FilterParams) -> list[str]`，按 spec F30 顺序：
   - 起点 = `p.all` 副本
   - 过滤 1：去除 `ALL_AGENT_DISALLOWED_TOOLS`
   - 过滤 2：若 `p.source >= Source.USER`，再去除 `CUSTOM_AGENT_DISALLOWED_TOOLS`（本期为空）
   - 过滤 3：若 `p.background`，与 `ASYNC_AGENT_ALLOWED_TOOLS + [is_mcp_or_skill 命中的]` 取交集
   - 过滤 4：去除 `p.disallowed`
   - 过滤 5：若 `len(p.allowed) > 0`，与之取交集
4. 辅助函数 `is_mcp_or_skill(name: str) -> bool`：`name.startswith("mcp__")`

**验证：** `python -c "from novacode.tool.filter import apply_agent_tool_filter"` 导入无误

## T9: 工具过滤测试

**文件：** `tests/test_tool_filter.py`
**依赖：** T8
**步骤：**
1. 参数化测试 `apply_agent_tool_filter` 覆盖各组合：
   - 默认：无后台、无白名单、无黑名单 → 去 5 个元工具
   - 后台：取 `ASYNC_AGENT_ALLOWED_TOOLS` 交集，且 Agent / TaskList 等元工具被剔除
   - 黑名单：`disallowed=["bash"]` → 不含 bash
   - 白名单：`allowed=["read_file", "grep"]` → 仅这两个
   - 黑 + 白：白名单先收窄，黑名单再剔除
   - 后台 + MCP 工具：`mcp__xxx` 被保留
2. 单独测试 `is_mcp_or_skill` 边界

**验证：** `pytest tests/test_tool_filter.py -v` 通过

## T10: Agent 类扩展 - 新增构造参数

**文件：** `src/novacode/agent/__init__.py`
**依赖：** 无
**步骤：**
1. 在 `Agent.__init__` 关键字参数区（`hook_engine` 之后）加：
   ```python
   system_prompt: str | None = None,
   max_turns: int = 0,                                  # 0 = 用全局 MAX_ITERATIONS=25
   permission_mode: "Mode | None" = None,               # None = run_to_completion 用 Mode.DEFAULT
   dont_ask: bool = False,
   approval_upgrader: "ApprovalUpgrader | None" = None,  # 延迟导入类型（permission_upgrade 在 T11）
   ```
   存为实例字段（如 `self._system_prompt = system_prompt` 或公开字段，对齐现有 `self._*` 风格）
2. 改 `run` 的循环上限：`for it in range(1, MAX_ITERATIONS + 1)` → `for it in range(1, (self._max_turns or MAX_ITERATIONS) + 1)`
3. 改 `run` 的系统提示装配：`sys_text = prompt.build_system_prompt(...)` → `sys_text = self._system_prompt or prompt.build_system_prompt(...)`
4. docstring 解释每个选项语义
5. 新建 `tests/test_agent_options.py`：用 mock provider 构造带各参数的 Agent，断言字段生效、默认值正确、`run` 循环上限跟随 max_turns（用 mock provider 计数请求轮数）

**验证：** `pytest tests/test_agent_options.py -v` 通过

## T11: ApprovalUpgrader 类型

**文件：** `src/novacode/agent/permission_upgrade.py`
**依赖：** T10
**步骤：**
1. 新建文件，声明：
   ```python
   from typing import Awaitable, Callable
   from novacode.permission import Outcome
   from novacode.agent import ApprovalRequest   # 定义在 agent/__init__.py（函数内延迟导入防循环）

   ApprovalUpgrader = Callable[
       [ApprovalRequest],
       Awaitable[tuple[Outcome, bool]],
   ]

   async def default_upgrader(req: ApprovalRequest) -> tuple[Outcome, bool]:
       """默认实现：拒绝本次。子 Agent 的 Ask 工具被拒，原因回灌让它调整。"""
       return Outcome.DENY_ONCE, True
   ```
2. docstring 解释：`(outcome, ok)`——`ok=True` 时调用方按 outcome 走（ALLOW_ONCE/ALLOW_FOREVER 继续执行，DENY_ONCE 拒绝回灌）；`ok=False` 时调用方走默认 emit Approval 路径

**验证：** `python -c "from novacode.agent.permission_upgrade import default_upgrader"` 导入无误

## T12: Fork 路径辅助函数

**文件：** `src/novacode/agent/fork.py`
**依赖：** 无（纯函数）
**步骤：**
1. 新建 `fork.py`，声明常量与 `FORK_BOILERPLATE`（内容如 plan.md）
2. 实现 `build_forked_messages(parent_msgs: list[Message]) -> list[Message]`：
   - 深拷贝 `parent_msgs`（`copy.deepcopy`）
   - 扫描末尾 assistant 消息的 `tool_calls`：对每个未配对的 `tool_call_id`（后续无 ROLE_TOOL 回合消费），在克隆末尾追加一个 tool_results 回合（placeholder `ToolResult(content="[forked, skipped]", is_error=True)`）
   - 追加最后一条 user 消息：`content = FORK_BOILERPLATE`
   - **不注入任务文本**（由 run_to_completion add_user）
3. 实现 `is_fork_context(msgs: list[Message]) -> bool`：遍历 msgs，若 user 消息内容含 `FORK_BOILERPLATE_TAG` → True；默认 False

**验证：** `pytest tests/test_agent_fork.py -v` 通过（T13）

## T13: Fork 辅助函数测试

**文件：** `tests/test_agent_fork.py`
**依赖：** T12
**步骤：**
1. `build_forked_messages([])` → 返回单条 user 消息含 Boilerplate
2. parent 末尾有完整 assistant + tool_results 配对：cloned 末尾 == parent 末尾 + 一条 user
3. parent 末尾 assistant 有 2 个 tool_use 没配对：cloned 中追加 1 条 tool_results（2 个 placeholder）再追加 1 条 user
4. `is_fork_context`：消息中含 Boilerplate → True；不含 → False

**验证：** `pytest tests/test_agent_fork.py -v` 通过

## T14: `_execute_batched` 加 dont_ask 短路与 approval_upgrader

**文件：** `src/novacode/agent/__init__.py`
**依赖：** T10, T11
**步骤：**
1. 定位 `_execute_batched` **串行分支**（`else:` 块）的权限判定处：
   ```python
   decision, reason = self._engine.check(mode, call, False)
   ```
   在其后、`yield Event(tool=...)` 之前插入：
   ```python
   # ch13：子 Agent dontAsk 兜底（黑名单/沙箱/deny 规则已在前四层拦截，此处仅放行 Ask）
   if decision == Decision.ASK and self._dont_ask:
       decision = Decision.ALLOW
   ```
2. 在 `if decision == Decision.ASK:` 分支内、发 ApprovalRequest 之前插入：
   ```python
   # ch13：子 Agent 审批升级回调（默认拒绝本次；ok=False 走下方默认 emit 路径）
   if self._approval_upgrader is not None:
       req = ApprovalRequest(
           name=call.name, args=preview, reason=reason,
           respond=asyncio.get_running_loop().create_future(),
       )
       outcome, ok = await self._approval_upgrader(req)
       if ok:
           if outcome == Outcome.ALLOW_ONCE or outcome == Outcome.ALLOW_FOREVER:
               decision = Decision.ALLOW   # 落到下方执行
           else:
               # 拒绝本次：结果回灌（复用下方 DENY 分支? 或就地构造）
               results[i] = ToolResult(
                   tool_call_id=call.id,
                   content="用户拒绝本次操作（子 Agent 审批默认拒绝）",
                   is_error=True,
               )
               yield Event(tool=ToolEvent(name=call.name, args=preview, phase=Phase.START))
               yield Event(tool=ToolEvent(name=call.name, args=preview, phase=Phase.END,
                                          result=results[i].content, is_error=True))
               await self._dispatch_hook(HookEvent.POST_TOOL_USE, ...)  # 对齐既有拦截路径
               i += 1
               continue
   ```
   - 注：只读批永不 Ask（既有注释），无需处理
3. `approval_upgrader`/`dont_ask` 字段名与 T10 一致（如 `self._approval_upgrader` / `self._dont_ask`）

**验证：** `pytest tests/test_agent.py tests/test_agent_runtime.py -v` 现有测试不破

## T15: run_to_completion 实现

**文件：** `src/novacode/agent/run_to_completion.py`
**依赖：** T10, T14
**步骤：**
1. 新建文件，实现挂到 `Agent` 上的方法：
   ```python
   async def run_to_completion(
       self,
       conv: Conversation,
       task: str,
       events: asyncio.Queue | None = None,
   ) -> str:
       if task:
           conv.add_user(task)          # Fork 场景 conv 已含 Boilerplate + 任务,调用方传 "" 跳过
       mode = self._permission_mode or Mode.DEFAULT
       cancel = asyncio.Event()
       max_turns = self._max_turns or MAX_ITERATIONS
       reached_max = False
       async for ev in self.run(conv, mode, cancel):
           # 转发 tool / usage 事件（TaskManager / TUI 聚合用）
           if events is not None:
               if ev.tool is not None:
                   await events.put(ev.tool)
               if ev.usage is not None:
                   await events.put(ev.usage)
           if ev.notice == NOTICE_MAX_ITER:
               reached_max = True
           if ev.done:
               break
       # 返回 conv 末尾 assistant 文本（run 自然完成时已 add_assistant）
       final = _last_assistant_text(conv.messages())
       if reached_max:
           raise MaxTurnsReached(final)
       return final
   ```
2. 定义 `MaxTurnsReached(Exception)`（`__init__` 携带 `final_text`）
3. 定义 `_last_assistant_text(msgs)`：逆序找第一条 `role == "assistant"` 且有内容的消息
4. 在 `agent/__init__.py` 里把 `run_to_completion` 挂到 `Agent`（`from .run_to_completion import MaxTurnsReached` 或方法内延迟导入；注意 `run_to_completion.py` 需从 `agent/__init__.py` 拿 `NOTICE_MAX_ITER`/`MAX_ITERATIONS`/`Mode`——**用函数内延迟导入**避免模块级循环）
   - 推荐：`run_to_completion.py` 定义独立函数 `run_to_completion(self, ...)`，`agent/__init__.py` 顶部 `Agent.run_to_completion = run_to_completion`（方法赋值，避开循环）

**验证：** `pytest tests/test_run_to_completion.py -v` 通过（T16）

## T16: run_to_completion 测试

**文件：** `tests/test_run_to_completion.py`
**依赖：** T15
**步骤：**
1. mock provider 一个回合返回纯文本 → `run_to_completion` 返回该文本，无异常
2. 一个回合返回 tool_use（已知工具）+ 下一轮纯文本 → 工具被执行、final_text 正确
3. 模型一直调工具不出文本，`max_turns=3` → raise `MaxTurnsReached`（含最后文本）
4. `dont_ask=True` + 模型调一个 Ask 级工具（如 bash）→ 自动放行执行
5. `approval_upgrader` 回调被命中：子 Agent 设了 upgrader（mock 返回 ALLOW_ONCE），Ask 时 upgrader 被调用且工具执行；mock 返回 DENY_ONCE 时工具被拒
6. events 队列转发：运行子 Agent 时把 events 收集到 list，断言含 Tool 事件
7. `task=""` 时不 add_user；`task="x"` 时 conv 末尾出现 user=x

**验证：** `pytest tests/test_run_to_completion.py -v` 全部通过

## T17: Agent 工具实现

**文件：** `src/novacode/agent/agent_tool.py`
**依赖：** T8, T12, T15
**步骤：**
1. 新建文件，声明：
   ```python
   AUTO_BACKGROUND_SECONDS: float = 120.0

   @dataclass
   class AgentArgs:
       prompt: str
       description: str
       subagent_type: str = ""
       model: str = ""
       run_in_background: bool = False
       name: str = ""

   class AgentTool:
       read_only = False
       is_system = False
       def __init__(self, catalog, task_mgr,
                    parent: "Agent | None" = None,
                    bg_enabled: bool = True) -> None: ...
       def set_parent(self, ag: "Agent") -> None: ...
   ```
2. 实现协议方法：
   - `name(self) -> str: return "Agent"`
   - `description(self) -> str`：基础描述 + `subagent_type 可选值: " + ", ".join(d.name for d in catalog.list())`
   - `parameters(self) -> dict`：按 spec F1 写 JSON Schema（`prompt`/`description` required；`subagent_type`/`model`/`run_in_background`/`name` optional）
3. **循环依赖处理**：`agent_tool.py` 引用 `subagent`/`task`/`tool.filter`/`conversation`/`compact` 均模块级可（这些不 import agent）；构造子 Agent 时 `from ..agent import Agent` 用函数内延迟导入（agent → agent_tool 是包内同层，但 agent/__init__ 顶部若 import agent_tool 会循环——**不在 __init__ 顶部导入**，由 cli 从 `novacode.agent.agent_tool` 直接导入）
4. **execute 主流程**（见 plan.md）：解析 args → 校验 prompt/description → resolve 定义 → 决定后台 → 工具过滤 → 构造子 Agent（共享父 provider/registry/engine/hook_engine，独立 runtime）→ 后台 launch / 前台 wait_for(超时 adopt)
   - 工具过滤的 `all` 来源：`[d.name for d in parent_registry.definitions()]`
   - 子 Agent 的 `approval_upgrader=default_upgrader`（T11）
   - 前台超时后重起 `run_to_completion(sub_conv, "", events)`（conv 已含 task）
5. 实现辅助：`registry_names(reg)`（definitions 取 name）；events 聚合器协程（消费队列更新 PartialState）

**验证：** `pytest tests/test_agent_tool.py -v` 通过（T18）

## T18: Agent 工具测试

**文件：** `tests/test_agent_tool.py`
**依赖：** T17
**步骤：**
1. 缺 prompt → 返回错误
2. 未知 subagent_type → 返回 `unknown subagent_type: ...`
3. 已知 subagent_type（mock catalog 注入）→ 子 Agent 跑动并返回 final_text（mock provider）
4. `run_in_background=True` → 返回 `{"task_id":..., "status":"async_launched"}`（mock task_mgr）
5. `enable_subagent_background=False` 且后台路径 → 返回错误
6. Fork 路径（subagent_type 空）→ `fork_definition()` 命中、后台启动
7. `set_parent(None)` 时 execute 返回错误（未 wiring）

**验证：** `pytest tests/test_agent_tool.py -v` 全部通过

## T19: task 包基础结构

**文件：** `src/novacode/task/manager.py`
**依赖：** T15
**步骤：**
1. 新建包 `novacode.task`，加 `__init__.py` 与 `manager.py`
2. 声明 `Status(IntEnum)`：`RUNNING / COMPLETED / FAILED / CANCELLED`
3. 声明 `Usage` `@dataclass`（input/output/cache_write/cache_read）
4. 声明 `BackgroundTask` `@dataclass`（字段如 plan.md）
5. 声明 `PartialState` `@dataclass`（tool_count / last_activity / usage）
6. 声明 `Manager` 类：`_lock: asyncio.Lock; _tasks: dict; _by_name: dict; _done: asyncio.Queue; _counter: int`
7. 实现 `__init__` / `_next_id()`（`"task_" + secrets.token_hex(4)`）/ `get` / `list`（start_time 升序）/ `subscribe_done`

**验证：** `python -c "from novacode.task.manager import Manager"` 导入无误

## T20: Manager.launch 实现

**文件：** `src/novacode/task/manager.py`
**依赖：** T19
**步骤：**
1. 实现 `async def launch(self, ag, conv, name, task) -> str`：
   ```python
   task_id = self._next_id()
   bt = BackgroundTask(id=task_id, name=name, sub_agent=ag, conv=conv, task=task,
                       status=Status.RUNNING, start_time=time.monotonic())
   async with self._lock:
       self._tasks[task_id] = bt
       if name:
           self._by_name[name] = task_id      # 后启动覆盖前
   events = asyncio.Queue(maxsize=64)
   aggregator = asyncio.create_task(self._aggregate(events, bt))

   async def runner() -> None:
       try:
           text = await ag.run_to_completion(conv, task, events)
           bt.result = text
           bt.status = Status.COMPLETED
       except asyncio.CancelledError:
           bt.status = Status.CANCELLED
           raise
       except BaseException as e:             # N3：任何异常不崩主程序
           bt.status = Status.FAILED
           bt.err = e
       finally:
           bt.end_time = time.monotonic()
           aggregator.cancel()
           try:
               self._done.put_nowait(task_id)
           except asyncio.QueueFull:
               print(f"task manager: done queue full, dropping notification for {task_id}",
                     file=sys.stderr)

   bt.handle = asyncio.create_task(runner())
   return task_id
   ```
2. 实现 `_aggregate(queue, bt)`：消费队列，ToolEvent(Phase.START) 累加 `tool_count` + 更新 `last_activity`；Usage 累加到 `bt.usage`；队列收到 `None` sentinel 退出

**验证：** `pytest tests/test_task_manager.py::test_launch -v` 通过（T22）

## T21: Manager.adopt_running / stop / send_message / upgrade_approval

**文件：** `src/novacode/task/manager.py`
**依赖：** T20
**步骤：**
1. `async def stop(self, task_id) -> bool`：查 `_tasks` → `bt.handle.cancel()`；返回是否找到
2. `async def adopt_running(self, ag, conv, name, events, handle, partial) -> str`：
   - 前台超时后子 Agent 协程已被 wait_for cancel，调用方会**重新 `create_task` 起协程**并传入 handle
   - 注册 BackgroundTask（`status=RUNNING`，`handle` 用传入的 running task），把 `partial` 复制进 bt
   - 起聚合协程消费 events，等 running task 完成后写终态 + push done
   - 返回 task_id
3. `async def send_message(self, name, message) -> str`：
   - 查 `_by_name` → task_id；查 bt；`bt.status != COMPLETED` → raise `TaskBusy`
   - `bt.conv.add_user(message)`；`bt.status = Status.RUNNING`；`end_time` 清零
   - 重新起协程跑 `run_to_completion`（同 ag/conv，`task=""`）；跑完逻辑同 launch（同 id）
   - 返回 task_id
4. `async def upgrade_approval(self, req) -> tuple[Outcome, bool]`：返回 `(Outcome.DENY_ONCE, True)`（默认拒绝，spec 修订记录）

**验证：** `pytest tests/test_task_manager.py::test_stop -v` 通过

## T22: task 包测试

**文件：** `tests/test_task_manager.py`
**依赖：** T20, T21
**步骤：**
1. mock provider + mock agent 跑 `run_to_completion` → `launch` → `subscribe_done().get()` → `status=COMPLETED`、`result` 正确
2. 故意抛异常的 mock agent → `launch` → done 收到 → `status=FAILED`、`err` 非空
3. `stop`：`launch` 后立刻 `stop` → done 收到 → `status=CANCELLED`
4. `send_message`：`launch` + 等 COMPLETED → `send_message` 重新跑 → 拿到新结果（同 id）
5. `_by_name` 覆盖：`launch` 两次同 name → 后启动覆盖
6. `adopt_running`：mock 一个已启动的协程 + partial → adopt → 等 done → status 正确

**验证：** `pytest tests/test_task_manager.py -v` 全部通过

## T23: 4 个后台任务工具

**文件：** `src/novacode/task/tools.py`
**依赖：** T19, T20, T21
**步骤：**
1. 实现 `TaskListTool`：`name()="TaskList"`，`read_only=True`，`is_system=False`，`parameters()` 空对象；`execute` 返回 JSON 形如 `[{"id":"...","name":"...","status":"running","tool_count":3,"last_activity":"bash"}, ...]`
2. 实现 `TaskGetTool`：`name()="TaskGet"`，`parameters()` 含 `task_id` required；`execute` `get(id)` → 全字段 JSON；找不到 → `is_error=True`
3. 实现 `TaskStopTool`：`name()="TaskStop"`，`parameters()` 含 `task_id`；`execute` `await m.stop(id)` → `{"status":"cancellation_requested"}` 或错误
4. 实现 `SendMessageTool`：`name()="SendMessage"`，`parameters()` 含 `name` / `message`；`execute` `await m.send_message(name, msg)` → `{"task_id":"...","status":"resumed"}` 或错误
5. **`is_system=False`**（元工具对子 Agent 不可见——已由 `ALL_AGENT_DISALLOWED_TOOLS` 剔除）
6. 所有 execute 的 `args` 是 raw JSON 字符串，自己 `json.loads`

**验证：** `pytest tests/test_task_tools.py -v` 通过（T24）

## T24: 4 个工具的单测

**文件：** `tests/test_task_tools.py`
**依赖：** T23
**步骤：**
1. TaskList：`launch` 几个任务后调 → 返回 JSON 含所有（id/name/status/tool_count）
2. TaskGet：已知 id → 返回完整字段；未知 id → `is_error=True`
3. TaskStop：`stop` 一个 running task → 返回成功 + task 状态变 `CANCELLED`
4. SendMessage：`launch` 一个任务跑完 → `send_message` → 返回 `status:"resumed"` + 新结果

**验证：** `pytest tests/test_task_tools.py -v` 全部通过

## T25: config 加 enable_subagent_background

**文件：** `src/novacode/config.py`、`tests/test_config.py`
**依赖：** 无
**步骤：**
1. 在 `Config` `@dataclass` 加字段：
   ```python
   enable_subagent_background: bool | None = None  # YAML key: enableSubAgentBackground
   ```
2. `_from_dict` 读取：`raw.get("enableSubAgentBackground")`；`None` → 保持 None；否则宽松转 bool（复用 `_to_bool`）
3. 加方法：
   ```python
   def effective_enable_subagent_background(self) -> bool:
       return True if self.enable_subagent_background is None else self.enable_subagent_background
   ```
4. docstring 说明：默认 True；False 时所有 SubAgent 强制前台，Fork 路径报错（N6）
5. `tests/test_config.py` 补：缺省 → `effective_...()` 返回 True；显式 false → False；显式 true → True

**验证：** `pytest tests/test_config.py -v` 通过

## T26: TUI wiring

**文件：** `src/novacode/tui/run.py`
**依赖：** T6, T17, T19
**步骤：**
1. `run()` 加关键字参数：`task_mgr=None, subagent_catalog=None`
2. 构造主 `agent` 之后回填 Agent 工具 parent：
   ```python
   at = registry.get("Agent")
   if at is not None and hasattr(at, "set_parent"):
       at.set_parent(agent)
   ```
3. 主循环：每轮 `holder.conv.add_user(text)` 之前调用（若 `task_mgr` 非 None）：
   ```python
   await _inject_task_notifications(task_mgr, runtime)
   ```
   （`_inject_task_notifications` 在 T27 实现；本步先留调用点，T27 完成后编译通过）
4. 说明：ESC 切后台不做；子 Agent Ask 不弹窗（默认拒绝）

**验证：** `python -c "import novacode.tui.run"` 导入无误

## T27: task notification 注入

**文件：** `src/novacode/tui/tasks.py`
**依赖：** T19, T26
**步骤：**
1. 新建文件，实现：
   ```python
   def build_task_notification(bt) -> str:
       """F19 格式的 <task-notification> 块。"""
       name_part = f' (name="{bt.name}")' if bt.name else ""
       if bt.status == Status.COMPLETED:
           body = f"Result: {bt.result}"
       elif bt.status == Status.FAILED:
           body = f"Error: {bt.err}"
       else:
           body = f"Status: {bt.status}"
       return (f"<task-notification>\n"
               f"Task {bt.id}{name_part}: {bt.status.name.lower()}\n"
               f"{body}\n"
               f"</task-notification>")
   ```
2. 实现：
   ```python
   async def _inject_task_notifications(task_mgr, runtime) -> None:
       """非阻塞排空 done 队列,把完成通知注入 runtime.pending_reminders。"""
       q = task_mgr.subscribe_done()
       notifs: list[str] = []
       while True:
           try:
               task_id = q.get_nowait()
           except asyncio.QueueEmpty:
               break
           bt = task_mgr.get(task_id)
           if bt is None:
               continue
           notifs.append(build_task_notification(bt))
       if notifs and runtime is not None:
           runtime.append_reminders(notifs)
   ```
3. `tui/run.py` 导入并接线（T26 调用点补上）

**验证：** `pytest tests/test_tui.py -v` 通过（补一条：mock task_mgr 的 done 队列塞入完成 id，调 `_inject_task_notifications` 后断言 `runtime.pending_reminders` 含 `<task-notification>`）

## T28: Skill fork 改造 + launch_fork

**文件：** `src/novacode/agent/launch.py`、`src/novacode/skills/executor.py`
**依赖：** T15, T17
**步骤：**
1. 新建 `agent/launch.py`，声明：
   ```python
   @dataclass
   class ForkLaunchOpts:
       allowed_tools: list[str]
       conv: Conversation          # 已装填任务文本的 fork 对话
       provider: object            # llm.Provider（skills 可能已切 model）
       registry: object
       engine: object
       version: str
       hook_engine: object | None
       context_window: int = 200000

   async def launch_fork(opts: ForkLaunchOpts) -> str:
       """构造 Fork 子 Agent 并 run_to_completion 前台同步,返回 final_text。"""
       runtime = SessionRuntime(
           replacement=ContentReplacementState(),
           recovery=RecoveryState(),
           auto_tracking=CompactCircuitBreaker(),
           session=new_session_context(tempfile.gettempdir()),
           context_window=opts.context_window,
       )
       # 函数内延迟导入,避免循环
       from ..agent import Agent
       sub = Agent(
           opts.provider, opts.registry, opts.version, opts.engine,
           runtime=runtime,
           allowed_tools=opts.allowed_tools,
           permission_mode=Mode.BYPASS,   # Fork 后台无 UI 审批;allowed_tools 已收窄,黑名单/沙箱仍拦
           hook_engine=opts.hook_engine,
       )
       return await sub.run_to_completion(opts.conv, "", events=None)  # conv 已含任务
   ```
2. `skills/executor.py::_run_subagent` 改造：保留 provider/model 切换与 fork_conv 装填逻辑，把「构造 Agent + run」改为调 `launch_fork`（函数内延迟导入 `from ..agent.launch import ForkLaunchOpts, launch_fork`）；返回 final_text 行为不变（`_run_fork` 的 `ctx.conv.add_assistant(final)` 不动）
3. `AgentTool` 的 Fork 分支也可复用 `launch_fork`（可选，本期不强求——AgentTool 走 task_mgr 后台，逻辑不同）

**验证：** `pytest tests/test_skills_executor.py tests/test_skills_catalog.py -v` 现有测试不破

## T29: cli 装配

**文件：** `src/novacode/cli.py`
**依赖：** T17, T19, T23, T25, T26
**步骤：**
1. `_amain` 里 `hook.load(root)` 之后：
   ```python
   subagent_catalog = subagent.load_catalog(root)
   task_mgr = task.Manager()
   registry.register(task.TaskListTool(task_mgr))
   registry.register(task.TaskGetTool(task_mgr))
   registry.register(task.TaskStopTool(task_mgr))
   registry.register(task.SendMessageTool(task_mgr))
   registry.register(AgentTool(subagent_catalog, task_mgr, parent=None,
                               bg_enabled=cfg.effective_enable_subagent_background()))
   ```
   （顶部 `from . import subagent`、`from . import task as task_pkg`、`from .agent.agent_tool import AgentTool`）
2. `run(...)` 调用传 `task_mgr=task_mgr, subagent_catalog=subagent_catalog`
3. 注意：主 `agent` 在 `tui.run` 内构造，Agent 工具 parent 由 run 内 `set_parent` 回填（T26），cli 不需要 `set_parent`

**验证：** `python -m novacode --help` 不报错；`ruff check src/novacode/cli.py` 无告警

## T30: 集成测试 - 完整路径

**文件：** `tests/test_agent_tool_integration.py`（新增）
**依赖：** T17, T20, T29
**步骤：**
1. 端到端 mock：构造 mock provider 让主 Agent 调 Agent 工具（`subagent_type="Explore"`），子 Agent 也跑回纯文本
2. 验证 tool_result 包含子 Agent 的 final_text
3. 验证子 Agent 工具调用没看到 Agent / TaskList / TaskGet / TaskStop / SendMessage（过滤生效）
4. 验证后台路径：`run_in_background=True` → 立即返回 `async_launched` JSON，主 Agent 继续
5. 验证 `subagent.Catalog` 与 `apply_agent_tool_filter` 协同：`resolve` 拿 def，过滤函数按 `def.source / background / tools / disallowed_tools` 收窄

**验证：** `pytest tests/test_agent_tool_integration.py -v` 通过

## T31: 编译与综合测试

**依赖：** T1-T30
**步骤：**
1. `uv sync`（确保依赖装好）
2. `ruff check src/novacode/`
3. `ruff format --check src/novacode/`
4. `mypy src/novacode/`（可选）
5. `pytest tests/ -v`

**验证：** 全部命令通过，无失败用例

## 执行顺序

```
T1 → T2 → T3
       ↘
        T5 → T6 → T7
       ↗
       T4
T8 → T9
T10 → T11 → T14
T10 → T12 → T13
T14, T15 → T16
T8, T12, T15 → T17 → T18
T19 → T20 → T21 → T22
T19 → T20 → T23 → T24
T25
T6, T17, T19 → T26 → T27
T15, T17 → T28
T17, T19, T23, T25, T26 → T29
T17, T20, T29 → T30
所有 → T31
```
