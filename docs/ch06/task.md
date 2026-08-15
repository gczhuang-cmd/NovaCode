~~~Markdown
# 权限系统 Tasks

> 包名：`novacode`（Python 3.12+）。源码位于 `src/novacode/`，新增子包 `novacode.permission`。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/novacode/permission/__init__.py` | 包对外门面：导出 `Mode`/`Decision`/`Category`/`Outcome`/`Engine`/`new_engine`/`persist_local_allow` 等；`Mode` 四档 + `str`/`parse_mode` |
| 新建 | `src/novacode/permission/blacklist.py` | 内置危险命令正则集 + `hits_blacklist`（不可配，N1） |
| 新建 | `src/novacode/permission/sandbox.py` | `resolve_root`、`sandbox_ok`、`eval_symlinks_or_ancestor`（N2） |
| 新建 | `src/novacode/permission/rule.py` | `Rule`/`RuleSet`、`parse_rule`、`match`、`match_pattern`（glob） |
| 新建 | `src/novacode/permission/settings.py` | `Settings` YAML、`load_settings`、`to_rule_set`、`friendly_name`、`categorize`、`extract_target` |
| 新建 | `src/novacode/permission/engine.py` | `Engine`、`new_engine`、`check` 前四层流水线、`mode_fallback`、`start_mode` |
| 新建 | `src/novacode/permission/persist.py` | `rule_for`、`persist_local_allow`（写本地层文件） |
| 新建 | `tests/test_permission_blacklist.py` 等 | 黑名单/沙箱(含祖先回退)/规则/优先级/矩阵/加载降级/解析失败 单测 |
| 改   | `src/novacode/agent/__init__.py` | 删 `Mode`（迁 permission）；`Agent` 加 `engine`；`execute_batched(+mode)` 接入 `check`；`Event` 加 `approval` 字段；Ask 分支内联 `yield Event(approval=...)` + `await respond`；Deny 用 `ToolResult` 构造 |
| 改   | `tests/test_agent.py` | 权限集成(Allow/Deny/Ask/永久)、保序回灌、只读并发不退化、取消、模式迁移 |
| 改   | `src/novacode/tui/run.py` | `mode`→`permission.Mode`、`run` 增 `engine` 形参；`s-tab` 切换(`next_mode`)；`_stream_reply` 处理 `Event(approval=...)` 待批准块 + 内嵌读键；状态栏左侧常驻模式；`submit` 保留 `/plan`·`/do`（去掉 `/mode`） |
| 改   | `src/novacode/tui/view.py` | 待批准块渲染（多行三选菜单 + 光标高亮 `approval_block(req, cursor)`） |
| 改   | `tests/test_tui.py` | `next_mode` 循环切换、approval 事件渲染与读键回传、Esc 取消、状态栏常驻模式、模式跨轮保持；既有 `/plan`·`/do` 用例适配新 Mode 类型 |
| 改   | `src/novacode/cli.py` | 构造 `permission.new_engine(root)` 注入 `tui.run` |
| 改   | `examples/smoke.py` | 新增 `cwd`、构造引擎、`Mode.BYPASS` 运行；`Agent` 增 `engine` 实参 |
| 改   | `.gitignore` | 追加 `.novacode/settings.local.yaml` |
| 新建 | `.novacode/settings.yaml.example` | 权限配置示例（default_mode + allow/deny） |

---

## T1: permission 基础类型**文件：** `src/novacode/permission/__init__.py`
**依赖：** 无
**步骤：**
1. `class Mode(IntEnum)`：`DEFAULT/ACCEPT_EDITS/PLAN/BYPASS`（IntEnum 自动赋值）。
2. `Mode.__str__` → `"default"/"acceptEdits"/"plan"/"bypassPermissions"`。
3. `def parse_mode(s: str) -> tuple[Mode, bool]`：大小写不敏感识别四档名，未知返回 `(Mode.DEFAULT, False)`。
4. `class Decision(IntEnum)`：`ALLOW/DENY/ASK`。`class Category(IntEnum)`：`READ/WRITE/EXEC`。
5. `class Outcome(IntEnum)`：`DENY_ONCE/ALLOW_ONCE/ALLOW_FOREVER`（人在回路三选一）。
6. 在 `__init__.py` 暴露后续会被 agent/tui import 的符号：`Mode`、`Decision`、`Category`、`Outcome`、`Engine`、`new_engine`、`persist_local_allow`。

**验证：** `python -c "from novacode.permission import Mode, parse_mode; ..."` 跑通；`parse_mode` 对 `"default"/"acceptEdits"/"plan"/"bypassPermissions"`（含大小写变体）均得 `(对应档, True)`，`parse_mode("x")` 得 `(Mode.DEFAULT, False)`。

## T2: 危险命令黑名单**文件：** `src/novacode/permission/blacklist.py`
**依赖：** 无
**步骤：**
1. 模块级 `_BLACKLIST: list[re.Pattern] = [re.compile(...), ...]`，编译一组高危模式（见 plan：`rm -rf /|~|$HOME|/*`、`dd of=/dev/`、fork bomb、`mkfs.`、`> /dev/sd|nvme|disk`、`chmod -R 777 /` 等）。
2. `def hits_blacklist(command: str) -> bool`：`any(p.search(command) for p in _BLACKLIST)`。
3. 顶部 docstring 声明「启发式、非完备、不可配置放开」（N1）。

**验证：** 单测：`rm -rf /`、`rm -fr ~`、`:(){ :|:& };:`、`dd if=/dev/zero of=/dev/sda` 命中；`rm -rf ./build`、`git status`、`ls -la` 不命中。

## T3: 路径沙箱**文件：** `src/novacode/permission/sandbox.py`
**依赖：** 无
**步骤：**
1. `def resolve_root(root: str) -> str`：`Path(root).expanduser().resolve(strict=True)`（失败抛 `FileNotFoundError`）。
2. `def eval_symlinks_or_ancestor(abs_path: str) -> str`：对存在的目标 `Path.resolve(strict=True)`；不存在则逐级取最近**已存在祖先**目录 `resolve(strict=True)` 后拼回剩余段（覆盖「新建文件、含未创建中间目录」）。
3. `def sandbox_ok(engine: Engine, path: str) -> bool`：空 path 视为 `engine.root`；相对路径相对 `engine.root` 解析为绝对；`resolved = eval_symlinks_or_ancestor(abs_path)`；返回 `resolved == engine.root or resolved.startswith(engine.root + os.sep)`。用 `pathlib` / `os.sep`，不硬编码 `/`。

**验证：** 单测（`tmp_path` fixture 造 root + 内外文件 + 符号链接 via `Path.symlink_to`）：root 内文件通过；**root 内但含多级未创建中间目录的新建文件路径通过**（专测祖先回退分支）；`/etc/passwd`、`../outside`、root 内指向 root 外目录的软链接被拒。

## T4: 规则与匹配**文件：** `src/novacode/permission/rule.py`
**依赖：** 无
**步骤：**
1. `@dataclass class Rule: tool: str; pattern: str; allow: bool`；`@dataclass class RuleSet: allow: list[Rule]; deny: list[Rule]`。
2. `def parse_rule(s: str) -> tuple[Rule, bool]`：解析 `Tool(pattern)` 或 `Tool`；取友好名与括号内模式（可含空格/`*`/`**`）；非法（空、括号不配对）返回 `(Rule("","",False), False)`。
3. `def match_pattern(pattern: str, target: str) -> bool`：`pattern == ""`→`True`；命令串整串走「命令 glob」（`*` 匹配任意字符含空格，`**` 等价 `*`）；文件路径按 `/` 分段走 `*`（段内）/`**`（跨段）（参照 `tool/glob.py` 的 `match_segments`）。**实现**：将 glob 编译为正则后 `re.fullmatch`，或自实现段匹配。
4. `def RuleSet.match(self, friendly: str, target: str) -> tuple[Decision, bool]`：先遍历 `self.deny`（`r.tool == friendly and match_pattern(...)` 命中）→`(Decision.DENY, True)`；再 `self.allow`→`(Decision.ALLOW, True)`；否则 `(Decision.ALLOW, False)`（第二个 bool 表示是否命中）。

**验证：** 单测：`parse_rule("Bash(git *)")`、`parse_rule("Read")` 正确；`match_pattern("git *","git status")` 真、`"git *","npm i"` 假；`match_pattern("src/**","src/a/b.py")` 真、`"src/**","docs/x"` 假；同层 deny 与 allow 同时命中时 `match` 返回 `Decision.DENY`。

## T5: 配置加载与映射**文件：** `src/novacode/permission/settings.py`
**依赖：** T1, T4
**步骤：**
1. `@dataclass class Settings`：`default_mode: str = ""`、`permissions: PermissionsBlock = field(default_factory=...)`；`PermissionsBlock` 含 `allow: list[str]` / `deny: list[str]`。
2. `def load_settings(path: str) -> Settings`：文件不存在→空 `Settings`；读到则 `yaml.safe_load`，解析失败→抛 `SettingsError`（调用方降级，N5）。
3. `def to_rule_set(s: Settings) -> RuleSet`：`s.permissions.allow/deny` 各条 `parse_rule`，非法条目跳过；allow 条 `allow=True`、deny 条 `allow=False` 分别入 `RuleSet`。
4. `def friendly_name(internal: str) -> str`：`bash→Bash, read_file→Read, write_file→Write, edit_file→Edit, glob→Glob, grep→Grep`；未知原样返回。
5. `def categorize(internal: str, read_only: bool) -> Category`：`read_only→Category.READ`（优先）；否则 `write_file/edit_file→Category.WRITE`、其余（含 `bash`、未知工具）→`Category.EXEC`（N7 最严）。
6. `def extract_target(call: ToolCall) -> tuple[str, bool, bool]`：内部对 `call.input` 视情况 `json.loads`——`read_file/write_file/edit_file` 取 `path`（is_file=True）；`glob/grep` 取 `path`（**搜索根目录**，空→`"."`，is_file=True；注：`pattern`/`glob` 字段不参与沙箱）；`bash` 取 `command`（is_file=False）；未知工具→`("", False, False)`；**`json.loads` 失败或缺必填字段→`ok=False`**。

**验证：** 单测：缺失文件得空且不抛；非法 YAML 抛 `SettingsError`；`to_rule_set` 跳过非法条；`friendly_name`/`categorize`（含未知工具→EXEC、read_only 优先）/`extract_target`（各工具字段、解析失败 ok=False）各分支正确。

## T6: 引擎与前四层流水线**文件：** `src/novacode/permission/engine.py`
**依赖：** T1, T2, T3, T4, T5
**步骤：**
1. `@dataclass class Engine`（见 plan）：`root, blacklist, user/project/local RuleSet, local_path, start_mode`。
2. `def new_engine(root: str) -> tuple[Engine, Exception | None]`：
   - `try: root = resolve_root(root) except Exception as e: ...`；**失败时 `engine.root` 退化为传入 `root`、四层规则空、`start_mode=Mode.DEFAULT`，仍返回非 None `engine` + e**（cli 注入永不为 None，check 不抛）。
   - 加载三层：user=`~/.novacode/settings.yaml`（`Path.home()`）、project=`<root>/.novacode/settings.yaml`、local=`<root>/.novacode/settings.local.yaml`；各 `load_settings`→`to_rule_set`；**单个文件读/解析失败仅降级跳过该文件（视为空），绝不向上抛致命异常**。
   - `local_path = <root>/.novacode/settings.local.yaml`。
   - `start_mode`：依次取 local/project/user 的 `default_mode`（`parse_mode` 成功者优先 local），皆无→`Mode.DEFAULT`。
   - **唯一返回非 None err 的情形是 `resolve_root` 失败**。
3. `def mode_fallback(mode: Mode, cat: Category) -> Decision`：F5 矩阵——`cat == Category.READ` 或 `mode == Mode.BYPASS`→`ALLOW`；`mode == Mode.ACCEPT_EDITS and cat == Category.WRITE`→`ALLOW`；其余（default/plan 的 Write/Exec、acceptEdits 的 Exec）→`ASK`。**只产 Allow/Ask**。
4. `def Engine.check(self, mode: Mode, call: ToolCall, read_only: bool) -> tuple[Decision, str]`：
   - `cat = categorize(call.name, read_only)`；`friendly = friendly_name(call.name)`；`target, is_file, ok = extract_target(call)`。
   - ① `cat == Category.EXEC and target != "" and hits_blacklist(target)` → `(DENY, "命中危险命令黑名单：…")`。
   - ② `is_file`：`not ok` → `(DENY, "无法解析文件路径参数，安全拒绝")`；否则 `not sandbox_ok(self, target)` → `(DENY, "路径在项目目录之外：" + target)`。
   - ③ 三层规则合并为同一集合：先全局 `match_deny(friendly, target)` 命中→`(DENY, "匹配 deny 规则：…")`；再全局 `match_allow(friendly, target)` 命中→`(ALLOW, "")`；均未命中→继续（层级不参与裁决）。
   - ④ `mode_fallback(mode, cat)` → `(ALLOW, "")` 或 `(ASK, f"{mode} 模式下 {类别} 类操作需确认")`。
5. `Engine.start_mode -> Mode`：返回 `self._start_mode`。

**验证：** 单测：逐层短路（黑名单先于沙箱/规则；deny 规则先于模式；allow 规则不进模式）；跳层放行（非 EXEC 不被黑名单拦、Bash 不被沙箱拦）；模式矩阵逐档逐类断言（含 plan 行 Write/Exec→Ask）；效果优先级（任意一层的 deny 盖过其他层的 allow 等）；`resolve_root` 失败仍得非 None 引擎。

## T7: 会话与永久规则写入**文件：** `src/novacode/permission/persist.py`
**依赖：** T5, T6
**步骤：**
1. `def rule_for(call: ToolCall) -> tuple[Rule, str, bool]`：据 `extract_target` + `friendly_name` 生成**精确**规则（内存 Rule + YAML 串两种形态）——`bash`→`Bash(<command>)`；文件类→`Write(<relpath>)` / `Read(<relpath>)` 等（relpath = 相对 root 的 slash 路径）；bash 命令串经 `escape_glob` 转义字面 glob 元字符防止规则被泛化；解析失败/未知→`(Rule("","",False), "", False)`。
2. `def Engine.persist_local_allow(self, call: ToolCall) -> None`：`load_settings(self.local_path)`（缺失则空）→ 追加规则串到 `permissions.allow`（去重）→ `yaml.safe_dump` → 确保目录存在（`Path(...).parent.mkdir(parents=True, exist_ok=True)`）→ `Path.write_text(...)`；同步把规则并入 `self.local.allow`。异常向上抛，调用方（agent）捕获后只记日志不阻断。

**验证：** 单测（`tmp_path` fixture 作 root）：`persist_local_allow` 后 `local_path` 文件含该 allow 条、再 `new_engine` 重载仍 `ALLOW`；幂等：重复 `persist_local_allow` 不抛且不重复写文件。

## T8: agent 接入权限（模式迁移 + 判定 + 人在回路）**文件：** `src/novacode/agent/__init__.py`
**依赖：** T6, T7
**步骤：**
1. **模式迁移**：删除 agent 内 `Mode` 定义，`from novacode.permission import Mode`；`run` 形参 `mode: Mode`；`mode == Mode.PLAN` 处不变（defs 选只读、plan_reminder 注入）；`Mode.NORMAL` 引用全部改 `Mode.DEFAULT`。
2. `Agent.__init__` 加 `engine: Engine` 字段（`Agent(provider, registry, version="", engine=...)`，engine 必传）。
3. 新增 `@dataclass class ApprovalRequest: name: str; args: str; reason: str; respond: asyncio.Future[Outcome]`；`Event` 新增 `approval: ApprovalRequest | None = None` 字段（不引入联合体）。
4. **Ask 分支内联在 `execute_batched`**（async generator 无法从非 generator helper 内 yield）——串行分支 `ASK` 时：`respond = asyncio.get_running_loop().create_future()`；`yield Event(approval=ApprovalRequest(name=call.name, args=_args_preview(call.input), reason=reason, respond=respond))`；`watcher = asyncio.create_task(cancel.wait())`；`done, _ = await asyncio.wait([respond, watcher], return_when=asyncio.FIRST_COMPLETED)`；`watcher.cancel()`；若 `watcher in done`（用户取消）→ `respond.cancel()` + 走既有取消收尾（`completed=False`，回灌「已取消」）；否则 `outcome = respond.result()`。
5. `async def execute_batched(self, calls, mode, cancel)`（增 `mode` 形参）接入。**Deny 结果统一用 `ToolResult(tool_call_id=calls[k].id, content=reason, is_error=True)` 构造**：
   - 只读批：每个 `k` 先 `decision, reason = self.engine.check(mode, calls[k], True)`；按调用序发 `PhaseStart`；`decision == DENY`→ `results[k] = ToolResult(..., is_error=True)`、**不纳入并发 gather**；否则照旧并发（只读永不 Ask）。批完成后按调用序发 `PhaseEnd`（**Deny 项也发，is_error=True**，与有副作用 Deny 一致）。
   - 串行有副作用：`decision, reason = self.engine.check(mode, calls[i], False)`；`ALLOW`→`await tool.execute(...)`；`DENY`→`ToolResult(..., is_error=True)`；`ASK`→按步骤 4 拿 `outcome`，按 `outcome`：`ALLOW_ONCE`→执行；`ALLOW_FOREVER`→`try: self.engine.persist_local_allow(calls[i]) except Exception: logger.warning(...)` +执行；`DENY_ONCE`→被拒结果。
6. `run` 调 `self.execute_batched(summary.calls, mode, cancel)`。

**验证：** `python -c "from novacode.agent import Agent"` 不抛（配合 T9）；轻量自检：表驱动断言人在回路在 `cancel.set()` 时走取消收尾、不阻塞。

## T9: agent 单测**文件：** `tests/test_agent.py`
**依赖：** T8
**步骤：**
1. 既有 ch04/ch05 用例：`Agent(provider, registry, version, engine)` 增 `engine` 实参（`permission.new_engine(str(tmp_path))[0]`）；`Mode.NORMAL`→`Mode.DEFAULT`；fake provider 签名不变。
2. 新增：
   - **Deny 回灌不中断**：构造 deny（沙箱外路径）→ 模型请求该工具 → 工具结果 `is_error`、Loop 继续到次轮（脚本化 fake）。
   - **保序回灌**：单批含「被拒调用 + 放行调用」→ 断言结果按原 `calls` 下标序、各自 `tool_call_id` 正确配对（被拒 is_error、放行正常），不串位。
   - **Ask 人在回路**：default 下请求 `write_file` → 收 `Event(approval=req)` → 向 `req.respond` 调 `set_result(Outcome.ALLOW_ONCE)`/`DENY_ONCE`，断言执行/回灌生效。
   - **永久放行**：送 `ALLOW_FOREVER`，断言 `local_path` 文件被写、含 allow 条。
   - **只读并发不退化**：一批只读不产生任何 `Event(approval=...)`；被沙箱拦的只读得 errResult、其余仍并发完成。
   - **取消**：在 `Event(approval=...)` 等待中置 `cancel.set()` → Loop 干净收尾、历史合法、无挂起 task（`pytest-asyncio` 超时保护 + `asyncio.all_tasks()` 断言）。
   - **plan 迁移**：`Mode.PLAN` 仍只放只读工具、注入计划提醒（沿用 ch05 断言，类型换名）。

**验证：** `pytest tests/test_agent.py -q`；`pytest -q --timeout=30 tests/test_agent.py` 无超时；`python -X dev` 跑测试无 `RuntimeWarning: coroutine ... was never awaited`。

## T10: TUI 接入（模式切换 + 待批准态）**文件：** `src/novacode/tui/run.py`、`src/novacode/tui/view.py`
**依赖：** T8
**步骤：**
1. `run.py` 主循环：
   - `run(cfg, registry, engine)`：`mode = engine.start_mode()` 初始化；`_stream_reply(...)` 增传 `engine`；`_input_frame(...)` 增传 `mode` 与模式切换回调。
   - **Shift+Tab**：`_input_frame` 的 `KeyBindings` 加 `@kb.add("s-tab")`——回调调 `next_mode` 更新模式（经闭包可变容器）并 `console.print("已切换到 X 模式")` 后以空文本 `event.app.exit(result="")`（主循环 `if not cmd: continue` 分支重新渲染）。`next_mode(m: Mode) -> Mode` 为本模块纯函数：`Mode((int(m) + 1) % 4)`（DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT）。**流式时无输入框，天然仅空闲态生效**；切到 plan 不注入 `/do` 执行指令。
   - `submit` 分支：保留 `/plan`(→Mode.PLAN)、`/do`(→Mode.DEFAULT，固定回 default 并注入 `EXECUTE_DIRECTIVE`)、`/exit`；**不新增 `/mode`**。
2. **待批准块（`_stream_reply` 内）**：`async for ev in agent.run(...)` 遇 `ev.approval is not None` 时：
   - 暂停流式；`console.print` 渲染多行待批准块（Rich `Text`/`Group`：`● <name>` 加粗 + 缩进参数预览 + 灰字触发原因 + `是否继续?` + 三行菜单 `1. 允许本次 / 2. 永久允许（写入本地配置） / 3. 拒绝本次` + 底部灰字 `↑↓ 选择 · 回车确认 · Esc 取消`）。
   - 调内嵌 prompt_toolkit 极简输入框读键：`1`/`2`/`3` 直选、`↑`/`↓`（或 `j`/`k`）移动 `approve_cursor`（0..2，进入时重置 0，默认高亮「允许本次」）、`enter` 提交当前项；索引→`Outcome` 经 `outcome_for_index`（0=ALLOW_ONCE、1=ALLOW_FOREVER、2=DENY_ONCE）；选定后 `ev.approval.respond.set_result(outcome)` 并继续 `async for`。
   - 用户等待期间 `_CancelWatcher` 仍监听：Esc/Ctrl+C 置 `cancel` Event → agent 待批准等待 `watcher in done` → 走取消收尾（TUI 无需额外 set_result）。
3. `view.py`：新增 `approval_block(req: ApprovalRequest, cursor: int) -> Group`——渲染上述多行块，光标项 `> ` + 高亮色、其余 `  ` 前缀。
4. 状态栏（`_input_frame`）：左侧由 `f" {provider.name}"` 改为权限模式名——`Mode.DEFAULT`→`DEFAULT`(绿)、`Mode.ACCEPT_EDITS`→`ACCEPT EDITS`、`Mode.PLAN`→`PLAN`(黄)、`Mode.BYPASS`→`BYPASS`(红)；右侧 `provider.model` + token 用量不变。`render_banner` 的 ready hint 补「Shift+Tab 切换权限模式」。

**验证：** `python -m novacode` 启动可进 idle（合法配置）；自动化部分见 T11。

## T11: TUI 单测**文件：** `tests/test_tui.py`
**依赖：** T10
**步骤：**
1. 既有 `/plan`·`/do` 用例适配 `permission.Mode`（`Mode.PLAN`/`Mode.DEFAULT`）与 `run`/`_stream_reply` 增 `engine` 形参（测试内传 `permission.new_engine(str(tmp_path))[0]`）。
2. 新增（不引入 Textual，按项目既有测试风格——直接测纯函数与 `_stream_reply` 集成）：
   - **next_mode 循环**：断言 `next_mode` 从 `Mode.DEFAULT` 依次到 `ACCEPT_EDITS`→`PLAN`→`BYPASS`→`DEFAULT`。
   - **状态栏文本**：抽 `status_text(mode, model, usage_in, usage_out)` 纯函数，断言各模式显示 `DEFAULT`/`ACCEPT EDITS`/`PLAN`/`BYPASS` 且**不含 provider 名**。
   - **approval 集成**：用会发 `Event(approval=req)` 的 fake provider 走 `_stream_reply`，向 `req.respond.set_result(outcome)` 后断言工具执行/回灌生效（该路径与 agent 单测共享；TUI 侧重渲染与状态流转）。渲染断言 `approval_block(req, cursor)` 文本含工具名/原因/三选项、光标项带 `>`。
   - **取消**：待批准等待中置 `cancel` Event → `_stream_reply` 打印「已取消」、不抛错、不退出（覆盖 TUI 层取消渲染）。
   - **模式跨轮保持**：`mode` 为主循环局部变量、非 per-turn 创建，跨轮保持由结构保证；以 `next_mode` + run 代码结构审查 + agent 层 `mode` 传参断言覆盖。

**验证：** `pytest tests/test_tui.py -q`（pytest-asyncio）。

## T12: cli / smoke / 配置文件接线**文件：** `src/novacode/cli.py`、`examples/smoke.py`、`.gitignore`、`.novacode/settings.yaml.example`
**依赖：** T6, T8, T10
**步骤：**
1. `cli.py`：`root = str(Path.cwd().resolve())`；`engine, err = permission.new_engine(root)`；`if err is not None: print("权限引擎降级:", err, file=sys.stderr)` 后**继续**（`engine` 必非 None）；`asyncio.run(tui.run(cfg, registry, engine))`（沿用既有错误处理）。
2. `examples/smoke.py`：新增 `cwd = str(Path.cwd().resolve())`；`engine, _ = permission.new_engine(cwd)`；`agent = Agent(provider, registry, version="smoke", engine=engine)`；`agent.run(conv, Mode.BYPASS, asyncio.Event())`；docstring 更新（BYPASS + 引擎）。
3. `.gitignore`：在「NovaCode 运行配置」段追加 `.novacode/settings.local.yaml`。
4. `.novacode/settings.yaml.example`：示例——`default_mode: default`；`permissions.allow: ["Bash(git *)", "Bash(pytest)"]`；`permissions.deny: ["Bash(rm *)", "Read(.env)", "Write(.env)"]`；注释说明三层文件与优先级，并注明**只读类默认即 Allow，allow 规则主要用于提前放行 Bash/Write，deny 规则可对只读做围栏（如 Read(.env)）**。

**验证：** `python -m novacode --version` 不抛；`uv run python examples/smoke.py` 在含 write_file 的脚本下**不阻塞、跑完**（确认 `Mode.BYPASS` 跳过 Ask）；`python -m novacode` 能正常启动进对话。

## T13: 全量编译测试与规范**文件：** —
**依赖：** T1–T12
**步骤：**
1. `ruff format --check .`（通过；本地 `ruff format .` 已统一）。
2. `ruff check .`（无告警；`permission` 子包按本地包分组，import 顺序正确）。
3. `pytest`、`pytest --timeout=30 tests/test_agent.py tests/test_permission_*.py tests/test_tui.py`。
4. （可选）`mypy src/novacode` 通过（含 `permission` 子包）。
5. 确认 `.gitignore` 含 `.novacode/settings.local.yaml` 条目（项目当前**非 git 仓库**，`git check-ignore` 不可用，改查 `.gitignore` 内容）；检索输出无 api_key 明文。
6. **tmux 实跑冒烟**（CLAUDE.md 开发原则第 2 条）：default 下写文件触发 Ask 弹窗；Shift+Tab 循环到 `bypassPermissions` 后不再 Ask、状态栏左侧显示 `BYPASS`；`rm -rf /` 在 bypass 下仍被拦。

**验证：** 全部通过。

## 执行顺序

```
T1(类型) ─┬───────────────────────────────────┐
T2(黑名单)─┤                                    │
T3(沙箱) ──┤                                    ├─→ T6(引擎/流水线) ─→ T7(规则写入)
T4(规则) ──┴─→ T5(配置/映射) ───────────────────┘                          │
                                                                            │
                                              T6,T7 ─→ T8(agent 接入) ─┬─→ T9(agent 单测)
                                                                       ├─→ T10(TUI 接入) ─┬─→ T11(TUI 单测)
                                                                       │                  │
                                                          T6,T8,T10 ─→ T12(cli/smoke/配置)
全部 ─→ T13(ruff/pytest/mypy/tmux)
```
（依赖：T5←{T1,T4}；T6←{T1,T2,T3,T4,T5}；T7←{T5,T6}；T8←{T6,T7}；T9←T8；T10←T8；T11←T10；T12←{T6,T8,T10}；T13←全部。）
~~~