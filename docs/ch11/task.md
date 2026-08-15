~~~Markdown
# Skill 技能包系统 Tasks

> 包名：`novacode`（Python 3.12+）。源码位于 `src/novacode/`，内部模块以 `novacode.xxx` 导入；单测放 `tests/`，框架 `pytest`。
> ch11 修订：TUI 为 prompt_toolkit 非全屏 + `CommandContext`（无 UI Protocol / App）；`Registry` 在 `tool/__init__.py`；`build_system_prompt` 在 `prompt/modules.py`；打包后端 hatchling。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/novacode/skills/__init__.py` | 导出 `Catalog` / `ActiveSkills` / `Executor` / `SkillSource` 等 |
| 新建 | `src/novacode/skills/types.py` | `SkillMeta` / `Skill` / `SkillSource` / `ToolSpec` / `ActiveEntry` |
| 新建 | `src/novacode/skills/parser.py` | `parse_skill_dir`, frontmatter/body 分离, `tool.json` 解析 |
| 新建 | `tests/test_skills_parser.py` | 解析路径单测 |
| 新建 | `src/novacode/skills/catalog.py` | `Catalog`: `load / reload / register / get / list / names / validate_tools` |
| 新建 | `tests/test_skills_catalog.py` | 三层覆盖单测 |
| 新建 | `src/novacode/skills/active.py` | `ActiveSkills` 列表 |
| 新建 | `src/novacode/skills/render.py` | `render_body`（`$ARGUMENTS` + allowed_tools 提示） |
| 新建 | `src/novacode/skills/adapter.py` | `catalog_to_prompt_items` / `active_to_prompt_entries` |
| 新建 | `src/novacode/skills/install.py` | `install_from_url` + zip-slip 防护 |
| 新建 | `tests/test_skills_install.py` | zip-slip / 正常 zip 单测 |
| 新建 | `src/novacode/skills/executor.py` | `Executor.execute(ctx, name, args)`（inline / fork 分支） |
| 新建 | `src/novacode/skills/builtin/__init__.py` | 空包标识，供 `importlib.resources` 寻址 |
| 新建 | `src/novacode/skills/builtin/commit/SKILL.md` | 内置 commit Skill |
| 新建 | `src/novacode/skills/builtin/review/SKILL.md` | 内置 review Skill |
| 新建 | `src/novacode/skills/builtin/test/SKILL.md` | 内置 test Skill |
| 修改 | `src/novacode/tool/__init__.py` | `is_system` 标记 + `definitions_filtered` + `register_skill_tool` + `system_definitions` |
| 新建 | `src/novacode/tool/skill_tool.py` | `ToolSpec` 适配为 Tool（asyncio subprocess） |
| 新建 | `src/novacode/tool/load_skill.py` | LoadSkill 工具实现 |
| 新建 | `src/novacode/tool/install_skill.py` | InstallSkill 工具实现 |
| 修改 | `src/novacode/prompt/modules.py` | `optional_modules` / `build_system_prompt` 加 `skills_catalog` 参数 |
| 新建 | `src/novacode/prompt/skills_block.py` | `render_skills_catalog` / `render_active_skills_block` + 桥接类型 |
| 修改 | `tests/test_prompt.py` | 同步 `build_system_prompt` 签名变更 |
| 修改 | `src/novacode/command/command.py` | `Command` dataclass 增 `is_skill` 字段 |
| 修改 | `src/novacode/command/registry.py` | 新增 `remove_if` |
| 修改 | `src/novacode/command/context.py` | `CommandContext` 增 `catalog` 字段 |
| 修改 | `src/novacode/command/builtin_prompt.py` | 删除 `/review` 注册 |
| 修改 | `src/novacode/command/builtin_ui.py` | `handle_clear` 追加清空 active skills |
| 新建 | `src/novacode/command/builtin_skill.py` | `handle_skill`（Kind.LOCAL 列表输出） |
| 新建 | `src/novacode/command/skills.py` | `register_skills_as_commands` / `remove_skill_commands` |
| 修改 | `src/novacode/agent/runtime.py` | `SessionRuntime.active_skills` 字段 |
| 修改 | `src/novacode/agent/__init__.py` | Agent 构造 `catalog`/`allowed_tools` 参数 + `run()` 拼 sys/env |
| 修改 | `src/novacode/tui/run.py` | `run()` 接受 `catalog`，注入 `CommandContext` |
| 修改 | `src/novacode/cli.py` | 启动期接线 |
| 修改 | `pyproject.toml` | 增 `httpx` 依赖；hatchling include `SKILL.md` 资源 |

## T1: skills 包数据结构
**文件**：`src/novacode/skills/types.py`、`src/novacode/skills/__init__.py`
**依赖**：无
**步骤**：
1. `SkillSource(Enum)`：`BUILTIN/USER/PROJECT`，value `"builtin"/"user"/"project"`，`__str__` 返回 `self.value`
2. `@dataclass SkillMeta`（name、description、`allowed_tools: list[str]=field(default_factory=list)`、`mode: Literal["inline","fork"]="inline"`、`fork_context: Literal["none","recent","full"]="none"`、`model: str | None = None`）+ `is_fork()`
3. `@dataclass ToolSpec`（name、description、input_schema: dict、command: list[str]、base_dir: Path）
4. `@dataclass Skill`（meta、prompt_body、source_dir: Path、source: SkillSource、tool_specs: list[ToolSpec]）
5. `@dataclass ActiveEntry`（name、body）
6. `__init__.py` 暴露上述类型（Catalog/ActiveSkills/Executor 后续填充）

**验证**：`python -c "from novacode.skills import SkillMeta, Skill, SkillSource"` 通过；`ruff check src/novacode/skills/` 无告警。

## T2: SKILL.md 与 tool.json 解析
**文件**：`src/novacode/skills/parser.py`
**依赖**：T1，`pyyaml`
**步骤**：
1. `parse_skill_dir(dir_path, source) -> Skill`：读 `SKILL.md`（缺抛 `FileNotFoundError`）；分离 frontmatter/body；`yaml.safe_load` → `SkillMeta`（校验 name 正则 `^[a-z][a-z0-9-]*$` 长度 1-32、description 非空、mode/fork_context 取值，非法 mode 按 inline + warning）；读 `tool.json`（可选）→ `list[ToolSpec]`，`base_dir = dir_path.resolve()`
2. `_parse_frontmatter_and_body(data) -> (meta_dict, body)`
3. `_parse_tool_json(data, base_dir) -> list[ToolSpec]`

**验证**：`python -c "from novacode.skills.parser import parse_skill_dir"` 通过；`ruff check` 无告警。

## T3: 解析单测
**文件**：`tests/test_skills_parser.py`
**步骤**：minimal / invalid_name（大写抛 ValueError）/ with_tool_json / no_skill_md（FileNotFoundError）

**验证**：`pytest tests/test_skills_parser.py -v` 全绿。

## T4: Catalog 三层加载与覆盖
**文件**：`src/novacode/skills/catalog.py`
**依赖**：T1, T2
**步骤**：
1. `class Catalog`：`_lock = threading.RLock()`、`_by_name: dict`、`_order: list`
2. `register(s)`：加锁覆盖、维护 `_order` 按 name 字典序不重复
3. `get(name) / list() / names()`
4. `@classmethod load(work_dir)`：`_load_builtin_into`（T5 后接入）+ `_load_dir_into(home/.novacode/skills, USER)` + `_load_dir_into(work_dir/.novacode/skills, PROJECT)`
5. `_load_dir_into(c, base_dir, source)`：目录不存在静默跳过；遍历子目录 `parse_skill_dir` → `register`；解析失败打 stderr 跳过
6. `reload(work_dir) -> (added, removed)`：构造新 catalog 原子替换，返回差异
7. `@dataclass ValidationIssue(skill_name, tool_name)`
8. `validate_tools(reg) -> list[ValidationIssue]`：遍历 allowed_tools 逐项 `reg.get`；`load_skill`/`install_skill` 视为允许引用

**验证**：`python -c "from novacode.skills.catalog import Catalog"` 通过。

## T5: 内置三个 Skill 资源
**文件**：`builtin/__init__.py`、`builtin/commit/SKILL.md`、`builtin/review/SKILL.md`、`builtin/test/SKILL.md`、`pyproject.toml`
**依赖**：T4
**步骤**：
1. 写三个 SKILL.md（frontmatter：commit inline + `[bash, read_file, grep]`；review fork none + `[read_file, grep, glob, bash]`；test inline + `[bash, read_file, grep, glob]`），正文按 SOP 写、含 `$ARGUMENTS`
2. `_load_builtin_into(c)`：`importlib.resources.files("novacode.skills.builtin")` 遍历子目录，直接读 `SKILL.md` 内容构造 `Skill`（source=BUILTIN，source_dir 指向包资源路径）——**不解压 cache**（本期内置无 tool.json）
3. `Catalog.load` 接入 `_load_builtin_into`
4. `pyproject.toml`（hatchling）：`dependencies` 增 `httpx>=0.27`；`[tool.hatch.build.targets.wheel]` 增 `include = ["src/novacode/skills/builtin/**/SKILL.md"]` 保证资源进 wheel

**验证**：`python -c "from novacode.skills.catalog import Catalog; print(Catalog.load(Path('.')).names())"` 输出 `['commit', 'review', 'test']`。

## T6: Catalog 单测
**文件**：`tests/test_skills_catalog.py`
**步骤**：builtin_only / user_override（monkeypatch HOME）/ project_override / validate_tools_missing_tool

**验证**：`pytest tests/test_skills_catalog.py -v` 全绿。

## T7: ActiveSkills 列表
**文件**：`src/novacode/skills/active.py`
**步骤**：`activate(name, body)`（重复 name 更新 body 保位置）、`clear()`、`snapshot()`（拷贝）、`names()`；`_lock = threading.Lock()`

**验证**：`tests/test_skills_active.py` 覆盖 activate/clear/snapshot。

## T8: Render 渲染
**文件**：`src/novacode/skills/render.py`
**步骤**：`render_body(s, args)`——allowed_tools 非空时 body 顶部插提示（`\n\n---\n\n` 分隔）；`$ARGUMENTS` 替换；无占位符且 args 非空时末尾追加 `\n\n## User Request\n\n<args>`

**验证**：`tests/test_skills_render.py` 覆盖 4 种组合（有/无 placeholder × 有/无 args）。

## T9: prompt 包适配器
**文件**：`src/novacode/skills/adapter.py`
**步骤**：`@dataclass(frozen=True) PromptItem(name, description)` / `PromptEntry(name, body)`；`catalog_to_prompt_items(c)` / `active_to_prompt_entries(a)`

**验证**：`ruff check src/novacode/skills/` 无告警。

## T10: prompt 模块槽位
**文件**：`src/novacode/prompt/modules.py`
**步骤**：`optional_modules(instructions, memory, skills_catalog)`；priority 90 槽位由「已激活 Skill」改名 `skills-catalog`，content 取 `skills_catalog` 参数；`build_system_prompt(instructions, memory, skills_catalog)` 同步

**验证**：`ruff check src/novacode/prompt/modules.py` 通过（调用处报错留 T12 修复）。

## T11: prompt 新增 Skill 渲染函数
**文件**：`src/novacode/prompt/skills_block.py`
**步骤**：`SkillCatalogItem(name, description)` / `ActiveSkillEntry(name, body)`；`render_skills_catalog(items)`（空返回 ""，否则 `## Available Skills` 列表 + LoadSkill 引导）；`render_active_skills_block(entries)`（空返回 ""，否则 `## Active Skills` + `### Skill: <name>` + body）

**验证**：`python -c "from novacode.prompt.skills_block import render_skills_catalog; print(repr(render_skills_catalog([])))"` 输出 `''`。

## T12: build_system_prompt 签名更新
**文件**：`src/novacode/prompt/modules.py`
**步骤**：`build_system_prompt(instructions, memory, skills_catalog)` 把第三参传给 `optional_modules`

**验证**：`python -c "import novacode.prompt"` 通过；`ruff check src/novacode/prompt/` 通过。

## T13: prompt 单测同步
**文件**：`tests/test_prompt.py`
**步骤**：所有 `build_system_prompt(X, Y)` 改 `build_system_prompt(X, Y, "")`；新增 skills_catalog 非空用例；新增 render_skills_catalog / render_active_skills_block 空/非空用例

**验证**：`pytest tests/test_prompt.py -v` 全绿。

## T14: ToolRegistry 系统工具支持
**文件**：`src/novacode/tool/__init__.py`
**步骤**：
1. `Tool` Protocol 新增 `is_system: bool` 属性（默认 False）；6 个内置工具与 MCP 适配器补 `is_system = False`（dataclass 字段或 property）
2. `Registry.definitions_filtered(allowed)`：按 order 遍历，name 在 allowed 集合内 OR `tool.is_system` 时纳入
3. `Registry.register_skill_tool(t)`：重复名静默覆盖
4. `Registry.system_definitions()`：仅系统工具

**验证**：`pytest tests/test_tool.py -v` 通过；`python -c "from novacode.tool import Registry; Registry().definitions_filtered([])"` 不报错。

## T15: ToolSpec 适配为 Tool
**文件**：`src/novacode/tool/skill_tool.py`
**步骤**：`new_skill_tool(name, description, input_schema, command, base_dir) -> Tool`——`execute` 用 `json.dumps(args).encode()` 作 stdin，`asyncio.create_subprocess_exec(*command, cwd=base_dir, stdin/stdout/stderr=PIPE)`，`asyncio.wait_for(proc.communicate(input=...), timeout=30)`，returncode != 0 视失败，stdout 作结果

**验证**：写最小单测，`echo "ok"` 脚本验证 `await execute(...)` 返回 "ok"。

## T16: LoadSkill 工具
**文件**：`src/novacode/tool/load_skill.py`
**依赖**：T4, T7, T14, T15
**步骤**：`LoadSkillTool(catalog, active, registry)`；`name="load_skill"`；`parameters` 为 `{"type":"object","properties":{"name":{...}},"required":["name"]}`；`read_only=True`、`is_system=True`；`execute(args)`：`catalog.get` → 不存在返回 `unknown skill: <name>` → 重读 SKILL.md（内置 importlib.resources，失败回退 + warning）→ `active.activate` → `register_skill_tool` 注册 tool_specs → 返回 `Skill <name> activated. SOP pinned to env context. N specialized tools registered.`

**验证**：`ruff check src/novacode/tool/load_skill.py` 通过；基础用例单测。

## T17: InstallSkill 工具
**文件**：`src/novacode/tool/install_skill.py`
**依赖**：T18
**步骤**：`InstallSkillTool(catalog, work_dir)`；`name="install_skill"`；`read_only=False`、`is_system=False`；`execute`：`await install_from_url(args["source"], catalog, work_dir)`，返回成功消息

**验证**：`ruff check` 通过；功能在 T18 后集成测试。

## T18: install_from_url 与 zip-slip 防护
**文件**：`src/novacode/skills/install.py`
**依赖**：T4
**步骤**：`install_from_url(source, catalog, work_dir)`——`httpx.AsyncClient(timeout=60)` 流式下载到临时文件（>50MB 抛 `ValueError("zip too large")`）；`zipfile` 打开；计算顶层目录名并校验命名；遍历条目拒绝 `..`/绝对路径/symlink（`external_attr >> 16 & 0o170000 == 0o120000`）；解压到 `~/.novacode/skills/<top_dir>/`；`catalog.reload(work_dir)`；返回 `top_dir`

**验证**：`tests/test_skills_install.py`——zip_slip（含 `../../bad` → ValueError 含 "unsafe path"）+ happy（本地 http server 返回正常 zip，`catalog.get(top_dir)` 可拿到）。

## T19: Skill Executor（inline + fork）
**文件**：`src/novacode/skills/executor.py`
**依赖**：T7, T8, T14
**步骤**：`class Executor` 持有 catalog/active/registry/provider/eng/version/runtime
1. `async def execute(self, ctx, name, args) -> None`：
   - `skill = catalog.get(name)`；None → `ctx.console.print(f"skill not found: {name}")`
   - 重读 SKILL.md（失败回退 + warning）；`rendered = render_body(skill, args)`
   - inline：`ctx.conv.add_user(rendered)` + `await ctx.run_callback()`
   - fork：
     - 子 Conversation：`fork_conv = Conversation()`（无 writer 回调）；按 fork_context 填初始消息（none=仅 rendered；recent=`ctx.conv.messages()[-5:]` + rendered；full=与 recent 等价 + stderr warning）
     - provider：默认主 provider；`skill.meta.model` 非空时 `llm.new_provider(model)`
     - 子 Agent：`Agent(provider, registry, version, engine, runtime=None, allowed_tools=skill.meta.allowed_tools)`
     - `cancel = asyncio.Event()`；`async for ev in sub.run(fork_conv, Mode.DEFAULT, cancel)` 消费；累计 usage
     - 回写：`ctx.usage_box.usage_in/out += sub_usage`；`ctx.runtime.usage_anchor` 增量累加
     - `final_text = 子 conv 末尾 assistant 文本`；失败 → `f"[skill {name} failed: {reason}]"`
     - `ctx.conv.add_assistant(final_text)`
2. `Agent.__init__` 新增 `allowed_tools: list[str] | None = None`；`run()` defs 选择处若 `_allowed_tools` 非空用 `definitions_filtered`

**验证**：`tests/test_skills_executor.py`（mock provider + 假 ctx）；端到端 `/review`。

## T20: CommandContext 扩展
**文件**：`src/novacode/command/context.py`
**步骤**：`CommandContext` dataclass 增 `catalog: Any = None`（/skill 与 executor 访问 Catalog）；`runtime` 字段已含 `active_skills`

**验证**：`python -c "from novacode.command import CommandContext"` 通过；既有测试（test_command_builtins / test_tui_commands）不破坏。

## T21: command 命令改动
**文件**：`src/novacode/command/builtin_prompt.py`、`builtin_ui.py`、`command.py`
**步骤**：
1. `builtin_prompt.py` 删除 `/review` 注册与 `handle_review`（REVIEW_DIRECTIVE 一并移除）
2. `builtin_ui.py` 的 `handle_clear` 在重建会话后追加 `if ctx.runtime is not None and ctx.runtime.active_skills is not None: ctx.runtime.active_skills.clear()`
3. `command.py` 的 `Command` dataclass 增 `is_skill: bool = False`
4. 新增 `/skill` 注册（见 T22）

**验证**：`pytest tests/test_command_builtins.py tests/test_tui_commands.py -v` 全绿（更新 review 相关断言）。

## T22: handle_skill 实现
**文件**：`src/novacode/command/builtin_skill.py`
**步骤**：`async def handle_skill(ctx)`——`skills = ctx.catalog.list()`（catalog 为 None 视为空）；空 → `ctx.console.print("No skills loaded.")`；否则首行 `Available skills (N):`，按 name 字典序逐条 `ctx.console.print(f"  /{name.ljust(20)} {description}")`，末行 `Type /<skill-name> to invoke a skill.`。在 `builtins.py` 的 `register_builtins` 中注册 `Command("skill", "列出已加载的 Skill", Kind.LOCAL, handle_skill)`

**验证**：`tests/test_command_skills.py` 覆盖。

## T23: register_skills_as_commands
**文件**：`src/novacode/command/skills.py`
**步骤**：
1. `def register_skills_as_commands(reg, catalog, executor)`：遍历 `catalog.list()`，每个注册 `Command(name=s.name, description=s.meta.description + " [skill]", kind=Kind.PROMPT, is_skill=True, handler=functools.partial(_run_skill, executor=executor, name=s.meta.name))`——`_run_skill` 调 `await executor.execute(ctx, name, "")`
2. `def remove_skill_commands(reg)`：`reg.remove_if(lambda c: c.is_skill)`

**验证**：`tests/test_command_skills.py` 覆盖。

## T24: command.Registry 删除 API
**文件**：`src/novacode/command/registry.py`
**步骤**：`def remove_if(self, pred)`——遍历 `_by_name` 收集命中键并删除，同步从 `_visible` 移除

**验证**：`pytest tests/test_command_registry.py -v` 通过。

## T25: SessionRuntime active_skills 字段
**文件**：`src/novacode/agent/runtime.py`
**依赖**：T7
**步骤**：`SessionRuntime` 增 `active_skills: ActiveSkills`；`reset_for_new_session` 追加 `self.active_skills.clear()`；依赖方向：agent import skills 包的 `ActiveSkills`（skills 不 import agent，无循环）

**验证**：`python -c "from novacode.agent.runtime import SessionRuntime"` 通过；test_agent_runtime 通过。

## T26: Agent 拼装 sys / env 改动
**文件**：`src/novacode/agent/__init__.py`
**依赖**：T9, T12, T25
**步骤**：
1. `Agent.__init__` 增 `catalog: Catalog | None = None`、`allowed_tools: list[str] | None = None`
2. 新增 `activate_skill(name, body)` / `clear_active_skills()`（转发 `runtime.active_skills`）
3. `run()` 起始处：`catalog_text = render_skills_catalog(...)`（catalog 非 None 时）；`sys_text = build_system_prompt(instruction, memory, catalog_text)`；`env_text = env.render()`，若 `runtime.active_skills` 非空则 `env_text += "\n\n" + render_active_skills_block(...)`
4. defs 选择：`allowed_tools` 非空时 `self._registry.definitions_filtered(self._allowed_tools)`（PLAN 模式仍 read_only）

**验证**：`pytest tests/test_agent.py -v` 通过。

## T27: tui/cli 接入
**文件**：`src/novacode/tui/run.py`、`src/novacode/cli.py`
**依赖**：T20, T25
**步骤**：
1. `tui/run.py` 的 `run()` 增 `catalog=None` 参数；构造 `CommandContext` 时传 `catalog=catalog`
2. `cli.py` 启动期接线（T28）

**验证**：`python -m novacode` 能起来。

## T28: cli 接线
**文件**：`src/novacode/cli.py`
**依赖**：T1-T27
**步骤**：
1. `from novacode.skills import Catalog, ActiveSkills, Executor`
2. `catalog = Catalog.load(root)`；`active = ActiveSkills()`；`runtime = SessionRuntime(..., active_skills=active)`（runtime 构造后挂上）
3. `registry.register(LoadSkillTool(catalog, active, registry))`；`registry.register(InstallSkillTool(catalog, root))`
4. `issues = catalog.validate_tools(registry)`；遍历打 stderr 并从 catalog 移除不合格 Skill
5. `executor = Executor(catalog, active, registry, provider, engine, version, runtime)`
6. `command.register_builtins(cmd_registry)`（/review 已删、含 /skill）；`command.register_skills_as_commands(cmd_registry, catalog, executor)`
7. `Agent(...)` 构造传 `catalog=catalog`；`run(..., cmd_registry=cmd_registry, catalog=catalog, executor=executor, ...)`（executor 供未来 reload 用，本期可只传 catalog）

**验证**：`python -m novacode` 全包启动；`ruff check src/novacode/` 无新增告警。

## T29: 启动冒烟
**文件**：无
**依赖**：T28
**步骤**：tmux 启动；`/help` 含 `/skill`、不含独立 `/review`、含 `/commit [skill]` `/review [skill]` `/test [skill]`；`/skill` 输出三行；`/exit` 退出

**验证**：观察输出符合预期。

## T30: 端到端验证场景
按 checklist.md 端到端场景在 tmux 实跑。

## 执行顺序

```
T1 → T2 → T3
  → T4 (依赖 T1,T2) → T5 (依赖 T4) → T6 (依赖 T4,T5)
  → T7 (依赖 T1) → T8 (依赖 T1) → T9 (依赖 T4,T7)

T10 → T11 (依赖 T10) → T12 (依赖 T10,T11) → T13 (依赖 T12)

T14 → T15 (依赖 T1,T14) → T16 (依赖 T4,T7,T14,T15) → T17 (依赖 T18)
T18 (依赖 T4)

T20 → T21 (依赖 T20) → T22 (依赖 T20) → T23 (依赖 T20,T22) → T24 (依赖 T23)

T25 (依赖 T7) → T26 (依赖 T9,T12,T25) → T27 (依赖 T20,T25)

T19 (依赖 T7,T8,T14) → T28 (依赖 T1-T27)

T29 (依赖 T28) → T30
```

可并行：T1-T9 链、T10-T13 链、T14-T18 链、T20-T24 链彼此独立直到 T25 合流。本期单会话顺序执行。
~~~
