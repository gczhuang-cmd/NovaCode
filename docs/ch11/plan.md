~~~Markdown
# Skill 技能包系统 Plan

## 架构概览

新增一个 `novacode.skills` 包承载所有 Skill 相关的"数据 + 加载 + 执行 + 激活态"逻辑，与现有 `novacode.command`、`novacode.tool`、`novacode.prompt`、`novacode.agent` 通过细窄接口交互。

按职责拆解：

- **novacode.skills**：核心包。包含数据结构（`Skill`、`SkillMeta`、`ToolSpec`、`ActiveEntry`）、`SKILL.md` 解析、`tool.json` 解析、Catalog 三层路径扫描与覆盖、Skill 执行器（inline / fork 分支）、`ActiveSkills` 跨轮列表、`$ARGUMENTS` 渲染、`InstallSkill` zip 解压（zip-slip 防护），以及通过 `importlib.resources` 随包分发的三个内置 Skill 资源
- **novacode.tool.load_skill**：新增 LoadSkill 工具实现。系统工具，永远可见，不受权限拦截
- **novacode.tool.install_skill**：新增 InstallSkill 工具实现。普通工具，受权限模式约束
- **novacode.tool.skill_tool**：把 `ToolSpec` 适配为 `Tool` 实现（asyncio subprocess exec）
- **novacode.tool.__init__**（Registry 所在）：扩展——`Tool` Protocol 增加 `is_system` 标记（默认 False）；新增 `definitions_filtered(allowed)` 白名单切片导出（系统工具豁免）；新增 `register_skill_tool` 动态注册专属工具
- **novacode.command**：扩展——`Command` dataclass 增 `is_skill` 字段；`Registry` 增 `remove_if`；新增 `register_skills_as_commands`（把 Catalog 注册为 Kind.PROMPT 命令）、`/skill` 命令（Kind.LOCAL）；删除内置 `/review`；`handle_clear` 追加清空 active skills
- **novacode.prompt**：扩展——`optional_modules` 增加 `skills_catalog` 参数（priority 90 槽位改承载第一层 Skill 目录列表）；新增 `skills_block.py`（`render_skills_catalog` / `render_active_skills_block` + 桥接类型）
- **novacode.agent**：扩展——`SessionRuntime` 新增 `active_skills: ActiveSkills` 字段；`Agent` 构造新增 `catalog` / `allowed_tools` 参数；`run()` 装配 sys/env 时拼入 skills-catalog 与 active-skills
- **novacode.tui.run + cli**：`cli.py` 负责启动期接线（Catalog 加载、工具注册、校验、命令注册、executor 构造）；`tui/run.py` 主循环把 catalog/executor 注入 `CommandContext`

> 修订说明：原文档以 Textual `UI` Protocol / `App` / `tui/app.py` / `prompt/prompt.py` / `tool/registry.py` 为蓝本。本项目实际是 prompt_toolkit 非全屏 + `CommandContext`（ch10），handler 与 executor 一律经 `CommandContext` 访问能力。以下所有模块设计均按实际结构重写。

## 核心数据结构

### SkillMeta

```python
# novacode/skills/types.py
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class SkillMeta:
    name: str
    description: str
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    fork_context: Literal["none", "recent", "full"] = "none"
    model: str | None = None

    def is_fork(self) -> bool:
        return self.mode == "fork"
```

约定：`mode` 为空或 "inline" 视作 inline；`mode == "fork"` 视作 fork；其它值打 warning 后按 inline 处理。`fork_context` 仅 fork 时生效，缺省 "none"。

### Skill

```python
from enum import Enum
from pathlib import Path

class SkillSource(Enum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"

@dataclass
class Skill:
    meta: SkillMeta
    prompt_body: str           # SKILL.md 去 frontmatter 后的正文（启动时缓存，执行时重读覆盖）
    source_dir: Path           # 绝对路径或包资源路径，重读 SKILL.md 用
    source: SkillSource        # BUILTIN / USER / PROJECT
    tool_specs: list["ToolSpec"] = field(default_factory=list)
```

### ToolSpec

```python
@dataclass
class ToolSpec:
    name: str                    # 工具名（与 frontmatter allowed_tools 用名一致）
    description: str
    input_schema: dict           # 标准 function calling JSON Schema
    command: list[str]           # argv，首元素相对 source_dir 解析（或绝对路径）
    base_dir: Path               # 工作目录（exec 时的 cwd），固定为 source_dir
```

### Catalog

```python
import threading

class Catalog:
    def __init__(self) -> None:
        self._lock = threading.RLock()    # reload 用
        self._by_name: dict[str, Skill] = {}
        self._order: list[str] = []       # 按 name 字典序

    @classmethod
    def load(cls, work_dir: Path) -> "Catalog": ...
    def reload(self, work_dir: Path) -> tuple[list[str], list[str]]: ...   # (added, removed)
    def register(self, s: Skill) -> None: ...
    def get(self, name: str) -> Skill | None: ...
    def list(self) -> list[Skill]: ...      # 按 order
    def names(self) -> list[str]: ...
    def validate_tools(self, reg) -> list["ValidationIssue"]: ...
```

`Catalog.load` 按顺序扫描：
1. 通过 `importlib.resources.files("novacode.skills.builtin")` 列出内置 Skill 目录并解析（`source=BUILTIN`）
2. `~/.novacode/skills/*` 子目录（`source=USER`）
3. `<work_dir>/.novacode/skills/*` 子目录（`source=PROJECT`）

后扫到的同名 `name` 覆盖前者（内置 < 用户 < 项目）。

**内置 Skill 简化**：三个内置 Skill 本期均无 `tool.json`（只用现有工具），故**不采用"启动时解压到 cache"方案**——直接用 `importlib.resources` 读取 `SKILL.md` 内容，`source_dir` 指向包资源路径（仅用于重读 SKILL.md，不参与 exec）。

### ActiveSkills

```python
import threading

@dataclass
class ActiveEntry:
    name: str
    body: str                    # 激活那一刻磁盘上的 SKILL.md 正文

class ActiveSkills:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ActiveEntry] = []   # 保持激活顺序
        self._index: dict[str, int] = {}        # 重复激活覆盖原位置内容

    def activate(self, name: str, body: str) -> None: ...
    def clear(self) -> None: ...
    def snapshot(self) -> list[ActiveEntry]: ...  # 拷贝出当前列表（env 装配用）
    def names(self) -> list[str]: ...
```

### Executor

```python
class Executor:
    def __init__(self, catalog, active, registry, provider, eng, version, runtime): ...
    # 入口：被 Slash 命令 handler 调用（Skill 命令 handler 闭包捕获 executor 与 skill.name）
    async def execute(self, ctx: "CommandContext", name: str, args: str) -> None: ...
```

- inline 路径直接经 `ctx.conv.add_user(rendered)` + `await ctx.run_callback()` 注入主对话
- fork 路径起子 Agent 跑完后 `ctx.conv.add_assistant(final_text)` 写回主对话

## 模块设计

### `novacode/skills/parser.py`
**职责**：解析单个 Skill 目录 → `Skill`
**对外接口**：`def parse_skill_dir(dir_path: Path, source: SkillSource) -> Skill`
**依赖**：`pyyaml`（已在 pyproject 依赖）
流程：读 `<dir>/SKILL.md` → 分离 frontmatter/body → `yaml.safe_load` → `SkillMeta`（校验 name/mode/fork_context）→ 读 `<dir>/tool.json`（可选）→ 组装 `Skill`。

### `novacode/skills/catalog.py`
**职责**：三层路径扫描与覆盖管理
**对外接口**：`load / reload / register / get / list / names / validate_tools`
**依赖**：`novacode.skills.parser`、`importlib.resources`

`validate_tools`：遍历 Catalog 中所有 Skill 的 `meta.allowed_tools`，确认每个名字都能在传入 `ToolRegistry` 里 `get` 到；`load_skill` / `install_skill` 视为允许引用（与系统工具豁免一致）。记录所有不通过项返回。

### `novacode/skills/active.py`
**职责**：`ActiveSkills` 列表（见核心数据结构）。

### `novacode/skills/render.py`
**职责**：把 Skill body 渲染为最终注入文本
**对外接口**：`def render_body(s: Skill, args: str) -> str`
逻辑：
- 替换所有 `$ARGUMENTS` 出现
- 若无占位符且 args 非空，在末尾追加 `\n\n## User Request\n\n<args>`
- 若 `allowed_tools` 非空，在 body 顶部插一段 ``This skill is designed to use only these tools: <list>. Prefer them over other tools when possible.\n\n---\n\n``

### `novacode/skills/adapter.py`
**职责**：桥接 prompt 包（避免 prompt 反向依赖 skills）
**对外接口**：`catalog_to_prompt_items(c) -> list[PromptItem]`、`active_to_prompt_entries(a) -> list[PromptEntry]`
`PromptItem(name, description)` / `PromptEntry(name, body)` 为 frozen dataclass，定义在 skills 包内。

### `novacode/skills/executor.py`
**职责**：inline / fork 分发与执行
**对外接口**：`Executor` 类（含 `execute(ctx, name, args)` async 方法）

inline 分支：
1. `skill = self.catalog.get(name)`；为 None → `ctx.console.print(f"skill not found: {name}")`，返回
2. 从磁盘重读 `SKILL.md`（内置经 importlib.resources；失败回退缓存并 warning）
3. `rendered = render_body(skill, args)`
4. `ctx.conv.add_user(rendered)` + `await ctx.run_callback()`

fork 分支：
1. 从 Catalog 取 Skill；重读 SKILL.md；`render_body`
2. 按 `fork_context` 构造子 Conversation（`Conversation()` 无 writer 回调，不写 JSONL）：
   - none：仅一条 user 消息（rendered）
   - recent：从主 conv `ctx.conv.messages()[-5:]` 拷贝最近 5 条，再追加 rendered
   - full：**与 recent 等价 + stderr warning**（留待 ch12 接入 compact 摘要）
3. 选 provider：默认主 provider；`skill.meta.model` 非空时 `llm.new_provider(model)` 重新构造
4. 构造子 Agent：`Agent(provider, registry, version, engine, runtime=None, allowed_tools=skill.meta.allowed_tools)`（runtime=None 用默认临时会话，不污染主 runtime）
5. `cancel = asyncio.Event()`；`async for ev in sub_agent.run(fork_conv, Mode.DEFAULT, cancel)` 消费事件，累计 usage
6. fork usage 累加到主会话：`ctx.usage_box.usage_in/out += sub`；`ctx.runtime.usage_anchor` 增量累加
7. 取子对话末尾 assistant 文本作为 final_text
8. `ctx.conv.add_assistant(final_text)` 写入主对话历史（触发 on_append 持久化）

任一步骤出错（异常或 `asyncio.CancelledError`）：`final_text = "[skill <name> failed: <reason>]"`，仍以 assistant 消息写入主对话。

### `novacode/skills/install.py`
**职责**：InstallSkill 的核心逻辑——下载 zip、校验路径、解压到 `~/.novacode/skills/`
**对外接口**：`async def install_from_url(source: str, catalog: Catalog, work_dir: Path) -> str`
流程：
1. 通过 `httpx.AsyncClient` 下载 source 到临时文件（限时 60s、限大小 50MB）
2. `zipfile.ZipFile` 打开
3. 严格校验：所有路径必须以 `<top_dir>/` 起头、`<top_dir>` 满足 F3 命名、内部不含 `..`、不含绝对路径、不含符号链接（`ZipInfo.external_attr` 高位判定 symlink）
4. 解压到 `~/.novacode/skills/<top_dir>/`
5. `catalog.reload(work_dir)` 触发热重载
6. 返回 `<top_dir>`

### `novacode/skills/builtin/*`
**职责**：内置三个 Skill 的资源文件
**结构**：

```
novacode/skills/builtin/
├── __init__.py             — 空文件，使 builtin 成为可被 importlib.resources 寻址的包
├── commit/SKILL.md
├── review/SKILL.md
└── test/SKILL.md
```

- commit: `mode=inline`, `allowed_tools=[bash, read_file, grep]`
- review: `mode=fork`, `fork_context=none`, `allowed_tools=[read_file, grep, glob, bash]`
- test: `mode=inline`, `allowed_tools=[bash, read_file, grep, glob]`

`pyproject.toml`（hatchling）需加 SKILL.md 资源 include。

### `novacode/tool/load_skill.py`
**职责**：LoadSkill 工具实现
**对外接口**：实现 `Tool` 协议（`is_system=True`、`read_only=True`）
`execute` 流程：
1. 解析 `args["name"]`
2. `catalog.get(name)` → 不存在返回 `unknown skill: <name>`
3. 重读 `SKILL.md`（内置 importlib.resources / 文件系统）
4. `active.activate(name, body)`
5. 把 `skill.tool_specs` 注册进 `registry`（`register_skill_tool`，重复名静默覆盖）
6. 返回 `Skill <name> activated. SOP pinned to env context. N specialized tools registered.`

### `novacode/tool/install_skill.py`
**职责**：InstallSkill 工具实现
**对外接口**：实现 `Tool` 协议（`read_only=False`、`is_system=False`）
`execute` 直接 `await install_from_url(...)`，返回成功消息或错误。

### `novacode/tool/skill_tool.py`
**职责**：把 `ToolSpec` 适配为 `Tool` 实现
**对外接口**：`def new_skill_tool(name, description, input_schema, command, base_dir) -> Tool`
`execute`：`json.dumps(args).encode()` 作 stdin；`asyncio.create_subprocess_exec(*command, cwd=base_dir, stdin=PIPE, stdout=PIPE, stderr=PIPE)`；`asyncio.wait_for(proc.communicate(input=...), timeout=30)`；stdout 作结果；returncode != 0 视失败。

### `novacode/tool/__init__.py`
**修改**：
- `Tool` Protocol 新增 `is_system: bool` 属性（默认 False）；现有 6 个工具与 MCP 工具返回 False
- 新增 `Registry.definitions_filtered(allowed: list[str])`（白名单 + 系统工具豁免）
- 新增 `Registry.register_skill_tool(t: Tool)`（动态注册专属工具，重复名静默覆盖）
- 新增 `Registry.system_definitions()`（仅系统工具，fork 时与 allowed 合并）

### `novacode/prompt/modules.py`
**修改**：
- `optional_modules(instructions, memory)` 改为 `optional_modules(instructions, memory, skills_catalog)`
- priority 90 槽位由「已激活 Skill」（原为空壳）改名为 `skills-catalog`，content 取 `skills_catalog` 参数（第一层：Skill 名字+描述列表文本，由调用方拼好传入）
- `build_system_prompt(instructions, memory, skills_catalog)` 同步第三参

### `novacode/prompt/skills_block.py`
**职责**：Skill 相关的 prompt 渲染函数与桥接类型
**对外接口**：
- `SkillCatalogItem(name, description)` / `ActiveSkillEntry(name, body)`（frozen dataclass）
- `render_skills_catalog(items) -> str`：items 空返回 ""；否则输出 `## Available Skills\n\n- <name>: <description>...\n\nCall the LoadSkill tool ...`
- `render_active_skills_block(entries) -> str`：entries 空返回 ""；否则输出 `## Active Skills\n\n### Skill: <name>\n\n<body>...`

### `novacode/agent/runtime.py`
**修改**：
- `SessionRuntime` 新增字段 `active_skills: ActiveSkills`
- `reset_for_new_session` 增加 `self.active_skills.clear()`
- 依赖方向：skills 包不 import agent；agent/runtime.py import skills 包的 `ActiveSkills`（无循环——skills 不依赖 agent）

### `novacode/agent/__init__.py`
**修改**：
- `Agent.__init__` 新增关键字参数 `catalog=None`、`allowed_tools: list[str] | None = None`
- `run()` 起始处：
  ```python
  catalog_text = ""
  if self._catalog is not None:
      items = [SkillCatalogItem(i.name, i.description) for i in catalog_to_prompt_items(self._catalog)]
      catalog_text = prompt.render_skills_catalog(items)
  sys_text = prompt.build_system_prompt(self._instruction_text, self._memory_text, catalog_text)
  env_text = env.render()
  if self._runtime is not None and self._runtime.active_skills is not None:
      entries = active_to_prompt_entries(self._runtime.active_skills)
      skills_block = prompt.render_active_skills_block(
          [ActiveSkillEntry(e.name, e.body) for e in entries]
      )
      if skills_block:
          env_text += "\n\n" + skills_block
  ```
- defs 选择处：`allowed_tools` 非空时用 `self._registry.definitions_filtered(self._allowed_tools)`（PLAN 模式仍走 read_only）
- 新增方法 `activate_skill(name, body)` / `clear_active_skills()`（转发到 `runtime.active_skills`）

### `novacode/command` 扩展
- `command.py`：`Command` dataclass 增 `is_skill: bool = False`
- `registry.py`：新增 `remove_if(pred)`（按谓词删 `_by_name`/`_visible`）
- 新建 `skills.py`：`register_skills_as_commands(reg, catalog, executor)`（每个 Skill 注册 `Command(name=skill.name, description=skill.description + " [skill]", kind=Kind.PROMPT, is_skill=True, handler=...)`，handler 用 `functools.partial(_run_skill, name=skill.name)` 显式绑定）；`remove_skill_commands(reg)`（`reg.remove_if(lambda c: c.is_skill)`）
- 新建 `builtin_skill.py`：`handle_skill(ctx)`——`ctx.catalog.list()` 输出列表
- `builtins.py`（builtin_prompt.py）：删除 `/review` 注册；`builtin_ui.py` 的 `handle_clear` 追加 `ctx.runtime.active_skills.clear()`；注册 `/skill`

### `novacode/command/context.py`
**修改**：`CommandContext` 增字段 `catalog: Any = None`（Catalog 引用，/skill 与 executor 用）。`runtime` 字段已含 `active_skills`。

### `novacode/tui/run.py` + `novacode/cli.py`
- `cli.py` 启动期接线：
  ```
  cli.main:
    ├─ registry = new_default_registry()
    ├─ mcp 工具注入（已有）
    ├─ cmd_registry = Registry(); register_builtins(cmd_registry)     # /review 已删，内置 11 条 + /skill = 12
    ├─ catalog = skills.Catalog.load(root)
    ├─ registry.register(LoadSkillTool(catalog, active, registry))
    ├─ registry.register(InstallSkillTool(catalog, root))
    ├─ issues = catalog.validate_tools(registry)
    │     不通过项 → 打 stderr warning + 从 catalog 移除
    ├─ executor = skills.Executor(catalog, active, registry, provider, engine, version, runtime)
    ├─ command.register_skills_as_commands(cmd_registry, catalog, executor)
    ├─ runtime.active_skills = active（构造 SessionRuntime 时注入）
    ├─ Agent 构造传 catalog=catalog
    └─ run(..., cmd_registry=cmd_registry, catalog=catalog, executor=executor, ...)
  ```
- `tui/run.py`：`run()` 接受 `catalog` 参数；构造 `CommandContext` 时注入 `catalog` 字段

## 模块交互

### 启动期

```
cli.main:
  ├─ registry 组装（工具 + MCP + LoadSkill + InstallSkill）
  ├─ catalog = Catalog.load(work_dir)
  ├─ catalog.validate_tools(registry) → 不合格 Skill 移除 + stderr
  ├─ executor = Executor(catalog, active, registry, provider, eng, version, runtime)
  ├─ cmd_registry：register_builtins（删 /review + 加 /skill）→ register_skills_as_commands
  └─ run(... catalog, executor ...) → CommandContext 注入 catalog
```

### Skill 显式调用（/commit）

```
user → submit → dispatch_slash → lookup("commit") → Skill 命令 handler
       → executor.execute(ctx, "commit", "")
            ├ inline: render → ctx.conv.add_user + ctx.run_callback → 主对话回合
            └ fork: render → 子 Agent.run → final_text → ctx.conv.add_assistant
```

### Skill 意图触发（自然语言）

```
user 输入 → agent.run loop
   ├ 每轮 sys_text 含 skills-catalog（第一层：名字+描述）
   └ LLM 调 LoadSkill({"name":"commit"})
        → LoadSkillTool.execute → active.activate("commit", body)
   下一轮 run()：env_text 拼入 active-skills 块 → Agent 看得到完整 SOP
```

### /clear

```
/clear handler → 重建会话（ch10 handle_clear）→ ctx.runtime.active_skills.clear()
下轮 env_text 中 active-skills 块为空
```

### Reload（InstallSkill 后）

```
InstallSkillTool.execute → install_from_url(...)
   └ 解压完毕 → catalog.reload(work_dir) 返回 (added, removed)
        → command.remove_skill_commands(cmd_registry) + command.register_skills_as_commands(cmd_registry, catalog, executor)
        → /help 与补全菜单立即同步
```

### Fork 模式

```
executor.execute (fork) →
   ┌──────────────────── 子 Agent ────────────────────┐
   │ 独立 Conversation（无 writer 回调，不写 JSONL）    │
   │ Agent(provider, registry, version, engine,        │
   │      runtime=None, allowed_tools=skill.meta.allowed_tools) │
   │ async for ev in sub_agent.run(fork_conv, DEFAULT, cancel)  │
   │ 累计 usage → 回写主会话 usage_box + usage_anchor   │
   └───────────────────────────────────────────────────┘
   ctx.conv.add_assistant(final_text)
```

## 文件组织

```
novacode/
├── pyproject.toml                    # dependencies 增 httpx；hatchling include SKILL.md 资源
├── src/novacode/
│   ├── cli.py                        # 启动期接线
│   ├── skills/                       # 新包
│   │   ├── __init__.py               # 导出 Catalog / ActiveSkills / Executor / SkillSource ...
│   │   ├── types.py                  # SkillMeta / Skill / SkillSource / ToolSpec / ActiveEntry
│   │   ├── parser.py                 # parse_skill_dir
│   │   ├── catalog.py                # Catalog: load / reload / register / get / list / names / validate_tools
│   │   ├── active.py                 # ActiveSkills
│   │   ├── render.py                 # render_body
│   │   ├── adapter.py                # catalog_to_prompt_items / active_to_prompt_entries
│   │   ├── executor.py               # Executor.execute (inline / fork)
│   │   ├── install.py                # install_from_url（zip-slip 防护）
│   │   └── builtin/                  # importlib.resources 资源
│   │       ├── __init__.py
│   │       ├── commit/SKILL.md
│   │       ├── review/SKILL.md
│   │       └── test/SKILL.md
│   ├── tool/
│   │   ├── __init__.py               # 修改：is_system + definitions_filtered + register_skill_tool + system_definitions
│   │   ├── load_skill.py             # 新：LoadSkill 工具
│   │   ├── install_skill.py          # 新：InstallSkill 工具
│   │   └── skill_tool.py             # 新：ToolSpec 适配 Tool
│   ├── command/
│   │   ├── command.py                # 修改：Command.is_skill 字段
│   │   ├── registry.py               # 修改：remove_if
│   │   ├── context.py                # 修改：CommandContext.catalog 字段
│   │   ├── builtin_ui.py             # 修改：handle_clear 追加清 active
│   │   ├── builtin_prompt.py         # 修改：删除 /review
│   │   ├── builtin_skill.py          # 新：/skill 命令 handler
│   │   └── skills.py                 # 新：register_skills_as_commands / remove_skill_commands
│   ├── prompt/
│   │   ├── modules.py                # 修改：optional_modules 加 skills_catalog 参数
│   │   └── skills_block.py           # 新：render_skills_catalog / render_active_skills_block / 桥接类型
│   ├── agent/
│   │   ├── runtime.py                # 修改：SessionRuntime.active_skills 字段
│   │   └── __init__.py               # 修改：Agent catalog/allowed_tools 参数 + run 拼 sys/env
│   └── tui/
│       └── run.py                    # 修改：接收 catalog，注入 CommandContext
├── tests/
│   ├── test_skills_parser.py
│   ├── test_skills_catalog.py
│   ├── test_skills_active.py
│   ├── test_skills_render.py
│   ├── test_skills_install.py
│   ├── test_skills_executor.py
│   ├── test_tool_skill_tool.py
│   ├── test_tool_load_skill.py
│   ├── test_prompt.py                # 修改：build_system_prompt 第三参
│   └── test_command_skills.py
└── docs/ch11/
    ├── spec.md / plan.md / task.md / checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 数据格式 | 仅 SKILL.md（frontmatter+body） | 解析路径单一；不引入 yaml/md 分离负担 |
| Skill 形态 | 必须是目录 | 与 tool.json/references 契合；扩展空间大 |
| 优先级覆盖 | 内置 < 用户 < 项目 | 与 npm/git 习惯一致 |
| 内置 Skill 分发 | `importlib.resources` 随 wheel 走 | 新机器装包即附带 |
| 内置 Skill 读取 | **不解压 cache**，直接 resources 读内容 | 本期三个内置无 tool.json，无需真实 exec 路径；简化实现 |
| 第一阶段注入位置 | system prompt 模块（priority 90 skills-catalog 槽位） | 享受 prompt cache 稳定前缀 |
| 第二阶段注入位置 | env context（`Agent.run` 起始装配） | 每次对话回合前感知最新激活 SOP；prompt cache 失效是设计意图 |
| LoadSkill 入参 | 仅 name | 与意图识别语义一致 |
| LoadSkill 权限 | read-only + 系统工具 | 无外部副作用；豁免 allowed_tools 支持嵌套 |
| InstallSkill 权限 | 普通工具，受权限模式约束 | 写盘+网络，必须走授权 |
| fork 模式实现 | Python 端起子 Agent（同进程 asyncio task） | 直接复用现成 `Agent.run` |
| fork_context 默认 | none | 隔离才是 fork 本意 |
| fork_context=full | **本期与 recent 等价 + warning** | 留待 ch12 接入 compact 摘要管道 |
| allowed_tools 在 inline 模式 | 仅 fail-fast + SOP 顶部提示 | 避免 inline 动态切换工具集生命周期复杂度；安全靠权限引擎兜底 |
| allowed_tools 在 fork 模式 | `definitions_filtered` 真过滤 | 保持 spec F28 承诺，收窄子 Agent 注意力 |
| Skill 与已有命令冲突 | 跳过加载 + warning | 保护内置命令可靠性 |
| 解析失败 | 跳过单个 Skill，warning，不阻断 | 与 instructions loader 一致容错 |
| 热加载 | InstallSkill 后主动 reload；execute 时重读 body | 改 SKILL.md 下次执行生效；新装 Skill 不重启 |
| Skill 列表数据流 | adapter 桥接，prompt 包不依赖 skills 包 | 避免循环依赖 |
| UI 接口 | **不建 UI Protocol**；扩展 `CommandContext`（catalog 字段）+ Executor 直接操作 ctx | 与 ch10 实际架构一致 |
| 闭包循环变量 | `functools.partial(handler, name=skill.name)` | Python 闭包后期绑定，循环里必须显式拷贝 |
| zip 下载 | `httpx.AsyncClient`（需新增依赖） | 流式读取易做 LimitReader 限大小 |
| 子进程 exec | `asyncio.create_subprocess_exec` + `wait_for(timeout=30)` | 不阻塞 event loop；与 bash 工具一致 |
| Skill 自身参数 | 本期 /<name> 零参数；`$ARGUMENTS` 为空 | 与 ch10 F7 一致；参数交互走后续轮次对话 |
| 打包后端 | hatchling | pyproject 现状；`[tool.hatch.build.targets.wheel]` 增 include |
~~~
