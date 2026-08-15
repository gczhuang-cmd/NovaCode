~~~Markdown
# 项目记忆与会话持久化 Tasks

> 包名：`novacode`（Python 3.12+）。源码位于 `src/novacode/`。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/novacode/compact/state.py` | session ID 格式变更、`SessionContext` 加 `session_dir`、`open_session_context`、`parse_session_time` |
| 修改 | `src/novacode/conversation.py` | `from_messages` 类方法、回调触发 |
| 修改 | `tests/test_conversation.py` | 回调测试 |
| 修改 | `src/novacode/prompt/modules.py` | `build_system_prompt` / `optional_modules` 签名变更（带默认值） |
| 修改 | `tests/test_prompt.py` | 新签名测试 |
| 新建 | `src/novacode/instructions/__init__.py` | 子包标识 |
| 新建 | `src/novacode/instructions/loader.py` | Loader 类、三层加载、@include 展开 |
| 新建 | `tests/test_instructions_loader.py` | @include 深度/环路/逃逸/缺失文件测试 |
| 新建 | `src/novacode/session/__init__.py` | 子包标识 |
| 新建 | `src/novacode/session/writer.py` | Writer、Entry、append、`open_existing` |
| 新建 | `src/novacode/session/list.py` | `list_sessions`、SessionInfo |
| 新建 | `src/novacode/session/load.py` | `load_session`、坏行跳过、孤立截断 |
| 新建 | `src/novacode/session/cleanup.py` | `clean_expired`、ID 时间戳解析 |
| 新建 | `tests/test_session.py` | JSONL 读写、列表、恢复、清理测试 |
| 新建 | `src/novacode/memory/__init__.py` | 子包标识 |
| 新建 | `src/novacode/memory/types.py` | NoteType、Note、UpdateAction |
| 新建 | `src/novacode/memory/store.py` | Store、笔记文件 CRUD、索引读写 |
| 新建 | `src/novacode/memory/manager.py` | Manager、`load_index`、`update_async` |
| 新建 | `src/novacode/memory/prompts.py` | 记忆更新 prompt 模板 |
| 新建 | `tests/test_memory.py` | 索引加载、操作执行、截断测试 |
| 修改 | `src/novacode/agent/__init__.py` | run 末尾触发记忆更新；新增 memory_manager/instruction_text/memory_text 参数 |
| 修改 | `src/novacode/agent/runtime.py` | SessionRuntime 新增 `turn_count` 字段 |
| 修改 | `src/novacode/tui/commands.py` | /resume 命令注册 |
| 新建 | `src/novacode/tui/resume.py` | 会话列表项、prompt_toolkit 选择界面、`do_resume_session` |
| 修改 | `src/novacode/tui/run.py` | /resume 命令分发、主循环替换会话状态 |
| 修改 | `src/novacode/cli.py` | 启动流程串联、退出收尾 |
| 修改 | `.novacode/config.yaml.example` | 配置示例补充说明 |

## T1: Session ID 格式变更**文件：** `src/novacode/compact/state.py`
**依赖：** 无
**步骤：**
1. 修改 `_new_session_id()`：格式从 `<unix_ts>-<8hex>` 改为 `YYYYMMDD-HHMMSS-<4hex>`。使用 `datetime.now().strftime("%Y%m%d-%H%M%S")` 拼接 `secrets.token_hex(2)` 生成的 4 字符随机十六进制
2. `SessionContext` 新增 `session_dir: str` 字段，值为 `<workspace>/.novacode/sessions/<session_id>`
3. 修改 `new_session_context`：先算 `session_dir`，`spill_dir` 改为 `os.path.join(session_dir, "tool-results")`
4. 新增 `open_session_context(workspace: str, session_id: str) -> SessionContext`：不创建目录，只检查目录存在后填充字段
5. 新增 `parse_session_time(session_id: str) -> datetime | None`：从 ID 前 15 位解析 `YYYYMMDD-HHMMSS`，供清理和排序使用；旧格式解析失败返回 None（/resume 不展示、清理不误删，N3）

**验证：** `pytest tests/test_compact_state.py` 通过；新 session ID 格式形如 `20260601-143022-a1b2`

## T2: Conversation 回调机制**文件：** `src/novacode/conversation.py`、`tests/test_conversation.py`
**依赖：** 无
**步骤：**
1. `Conversation.__init__` 接受 `on_append: Callable[[llm.Message], None] | None = None` 与 `on_replace: Callable[[list[llm.Message]], None] | None = None` 两个可选参数，保存为私有属性
2. 新增 `from_messages` 类方法：用 `list(msgs)` 拷贝初始化消息列表（不触发回调，恢复场景不重复写盘），传入回调
3. 在 `add_user`、`add_assistant`、`add_assistant_with_tool_calls`、`add_tool_results` 末尾调用 `self._on_append(msg)`（如果非 None）
4. `replace_history(msgs, notify=True)`：`notify=True`（默认）时在末尾调用 `self._on_replace(list(self._messages))`——layer2 摘要完成后的整体替换触发；layer1 渐进替换传 `notify=False` 不触发（避免每轮 AUTO 全量重写，F12）
5. 补充测试：验证回调被正确触发（含 `notify=False` 不触发），验证无回调时行为不变

**验证：** `pytest tests/test_conversation.py` 通过

## T3: 项目指令加载器**文件：** `src/novacode/instructions/__init__.py`、`src/novacode/instructions/loader.py`、`tests/test_instructions_loader.py`
**依赖：** 无
**步骤：**
1. 定义 `class Loader`：`project_root`、`user_home`、`max_depth`（默认 5）。`user_home` 缺省用 `os.path.expanduser("~")`
2. 实现 `load() -> str`：按优先级扫描三个路径，每个调 `_load_file`，结果用 `"\n\n"` 拼接
3. 实现 `_load_file(path, boundary, depth, visited)`：
   - 检查 `depth > max_depth` → 返回深度警告注释
   - 解析绝对路径（`os.path.realpath`），检查 visited → 环路警告
   - 检查绝对路径在 boundary 下（`os.path.commonpath` 或 `Path.is_relative_to`）→ 逃逸警告
   - 读取文件二进制，检查前 512 字节有 `b"\x00"` → 二进制警告
   - 逐行扫描，正则 `^@include\s+(.+)$` 匹配独占行 → 递归 `_load_file` 展开
   - 返回展开后的完整内容
4. 测试用例：三层加载优先级、@include 正常展开、5 层深度截断、环路检测、路径逃逸、缺失文件跳过、二进制文件跳过

**验证：** `pytest tests/test_instructions_loader.py` 通过

## T4: Session Writer**文件：** `src/novacode/session/__init__.py`、`src/novacode/session/writer.py`
**依赖：** T1（`session_dir` 字段）
**步骤：**
1. 定义 `@dataclass class Entry`（字段见 plan.md）
2. 实现 `Writer.__init__(session_dir)`：`os.makedirs(session_dir, exist_ok=True)`，以 `open(jsonl_path, "ab")` 打开 `conversation.jsonl`，保存 `threading.Lock`；`_model` 初始 `""`、`_first` 初始 `True`
3. 实现 `set_model(model)`：延迟注入模型名（provider 在 TUI 内选定后才可调用，R9）
4. 实现 `Writer.open_existing(session_dir)` 类方法：不创建目录，直接 append 模式打开；`_first` 置 `False`（已有内容的文件不再写 model 字段）
5. 实现 `append(msg, model, is_first)`：构造 Entry，`is_first` 时填充 `model` 字段，加锁 → `json.dumps(asdict(entry), ensure_ascii=False)` + `"\n"` → `file.write(...)` → `file.flush()` + `os.fsync(file.fileno())` → 解锁
6. 实现 `write_compact_marker()`：写入 `{"type":"compact","ts":<unix_ts>}\n`
7. 实现 `append_all(msgs)`：逐条调 `append`（model 传 `self._model`、is_first 传 `self._first`；open_existing 后 `_first` 已 False，不会误写 model）
8. 实现 `close()` + `__enter__` / `__exit__`：关闭文件句柄
9. 提供 `on_append(msg)` / `on_replace(msgs)` 两个方法（cli 注入为 Conversation 回调）：`on_append` 调 `append(msg, self._model, self._first)`（写入后 `_first` 置 False）；`on_replace` 调 `write_compact_marker()` + `append_all(msgs)`

**验证：** `python -c "from novacode.session.writer import Writer"` 不报错

## T5: 会话列表扫描**文件：** `src/novacode/session/list.py`
**依赖：** T1（`parse_session_time`）
**步骤：**
1. 定义 `@dataclass class SessionInfo`（字段见 plan.md）
2. 实现 `list_sessions(sessions_dir) -> list[SessionInfo]`：
   - `pathlib.Path(sessions_dir).iterdir()` 遍历子目录
   - 对每个目录：尝试 `parse_session_time(dir.name)` → 失败则跳过（旧格式）
   - 检查 `conversation.jsonl` 是否存在 → 不存在则跳过
   - 打开 JSONL，逐行读到第一条 `role == "user"` 的消息 → 取 `content` 作为 title（截断到 50 字符）
   - 从第一条消息的 `model` 字段提取 model
   - `Path.stat()` 获取 `st_size` 和 `st_mtime`
   - 按 `modified_at` 倒序排列返回

**验证：** `python -c "from novacode.session.list import list_sessions"` 不报错

## T6: 会话加载恢复**文件：** `src/novacode/session/load.py`
**依赖：** T4（Entry 类型）
**步骤：**
1. 实现 `load_session(session_dir) -> tuple[list[llm.Message], int | None]`：
   - 逐行读取 JSONL，`json.loads` 解析为 dict
   - 解析失败的行 `continue`（坏行容错）
   - 记录最后一个 `type == "compact"` 标记的行号
   - 从最后 compact 标记之后开始构建 `list[llm.Message]`，同时记录该段最后一条消息的 `ts`（`Entry.ts`）
   - 扫描结尾：如果最后一条是 assistant 且有 `tool_calls`，但后面没有 tool 消息 → 截断掉该条
   - 返回 `(messages, last_ts)`（无消息时 `last_ts` 为 None）
2. 提取 `_truncate_orphaned_tool_calls(msgs) -> list[llm.Message]` 为独立函数便于测试

**验证：** `python -c "from novacode.session.load import load_session"` 不报错

## T7: 会话过期清理**文件：** `src/novacode/session/cleanup.py`
**依赖：** T1（`parse_session_time`）
**步骤：**
1. 实现 `clean_expired(sessions_dir, max_age)`（**同步函数**，R12）：
   - 遍历子目录
   - `parse_session_time(dir.name)` → 返回 None 跳过（旧格式不清理，N3）
   - 时间距今超过 `max_age` → `shutil.rmtree(dir_path, ignore_errors=False)`
   - 单个删除失败 `logging.warning(...)` 继续
2. cli 侧启动时 `asyncio.create_task(asyncio.to_thread(clean_expired, sessions_dir, max_age))` 后台执行；进程退出时 await 收尾（T16）

**验证：** `python -c "from novacode.session.cleanup import clean_expired"` 不报错

## T8: Session 子包测试**文件：** `tests/test_session.py`
**依赖：** T4, T5, T6, T7
**步骤：**
1. `test_writer_append_and_read`：写入 3 条消息 → 逐行读回验证 JSON 结构
2. `test_writer_compact_marker`：写入消息 → compact 标记 → 新消息 → `load_session` 只返回 compact 后的
3. `test_load_session_bad_line_skip`：插入坏行 → 被跳过，其余正常
4. `test_load_session_orphaned_tool_calls`：末尾是带 `tool_calls` 的 assistant → 被截断
5. `test_list_sessions`：创建 3 个 session 目录 → 列表返回 3 项，按时间倒序
6. `test_list_sessions_skips_old_format`：混合新旧格式目录 → 只返回新格式
7. `test_clean_expired`：创建一个 31 天前和一个 1 天前的目录 → 只删 31 天前的

**验证：** `pytest tests/test_session.py` 通过

## T9: 笔记类型与存储**文件：** `src/novacode/memory/__init__.py`、`src/novacode/memory/types.py`、`src/novacode/memory/store.py`
**依赖：** 无
**步骤：**
1. `types.py`：定义 `NoteType`（StrEnum）、`Note`（dataclass）、`UpdateAction`（dataclass）
2. `store.py`：
   - `Store.__init__(dir)`：保存 `dir` 与 `threading.Lock()`
   - `ensure_dir()`：`os.makedirs(self._dir, exist_ok=True)`
   - `load_index() -> str`：读取 `MEMORY.md` 内容；不存在返回空字符串
   - `apply(actions)`：
     - create：拼装 frontmatter（用 `yaml.safe_dump` 生成或手写）+ content 写到 `<type>_<slug>.md`，在 `MEMORY.md` 追加一行
     - update：重写文件内容和 frontmatter（保留 created，更新 updated），更新 `MEMORY.md` 对应行
     - delete：`os.remove(...)`，从 `MEMORY.md` 移除对应行

**验证：** `python -c "from novacode.memory.store import Store"` 不报错

## T10: 记忆管理器**文件：** `src/novacode/memory/manager.py`、`src/novacode/memory/prompts.py`
**依赖：** T9
**步骤：**
1. `prompts.py`：定义 `MEMORY_UPDATE_SYSTEM_PROMPT` 字符串常量（中文），包含规则说明和 JSON 输出格式
2. `manager.py`：
   - `Manager.__init__(project_dir, user_dir, provider, model)`：内部建两个 `Store`，记录 `provider/model`，`asyncio.Lock` 保护并发更新
   - `load_index() -> str`：合并项目级和用户级索引（项目级在前、用户级在后），超 25KB 截断并追加 `(index truncated)`
   - `set_provider(provider, model)`：延迟设置（启动时 provider 未选定）
   - `async update_async(recent_msgs)`：
     - 进入 `async with self._lock`（防并发更新）
     - 构造记忆更新请求：`Request(messages=[Message(role="user", content=req_text)], tools=None, system=System(stable=MEMORY_UPDATE_SYSTEM_PROMPT))`，`req_text` 为「最近消息 + 两级现有索引」拼成的纯文本（F37/F38，tools=None 先例见 ch08 `summarize_once`）
     - `async for ev in provider.stream(req)` 收集 `ev.text` 拼完整回复；`ev.err` 直接 raise（外层捕获）
     - `json.loads` 解析 JSON 数组（LLM 返回可能带 Markdown fence 包裹，先剥离 ` ```json ` 围栏）
     - 按 `level` 字段分发到 `project_store.apply(...)` / `user_store.apply(...)`
     - 成功落盘后调用回调刷新记忆索引（F32，见 T15 第 4 步）
     - 任何失败 `logging.exception(...)` 不上抛

**验证：** `python -c "from novacode.memory.manager import Manager"` 不报错

## T11: Memory 子包测试**文件：** `tests/test_memory.py`
**依赖：** T9, T10
**步骤：**
1. `test_store_create_note`：apply create → 文件存在、frontmatter 正确、`MEMORY.md` 有对应行
2. `test_store_update_note`：apply update → 文件内容更新、`MEMORY.md` 对应行更新
3. `test_store_delete_note`：apply delete → 文件不存在、`MEMORY.md` 对应行消失
4. `test_manager_load_index`：两级各有索引 → 合并返回，项目级在前
5. `test_manager_load_index_truncate`：构造超 25KB 索引 → 截断 + `(index truncated)` 标注
6. `test_manager_update_async_parses_response`：mock provider 返回 JSON → 笔记文件被创建

**验证：** `pytest tests/test_memory.py` 通过

## T12: build_system_prompt 参数化**文件：** `src/novacode/prompt/modules.py`、`tests/test_prompt.py`
**依赖：** 无
**步骤：**
1. `prompt/modules.py`：`optional_modules` 改为 `optional_modules(instructions: str = "", memory: str = "") -> list[Module]`，用参数填充「自定义指令」(80) 与「长期记忆」(100) 两个槽的 content；空字符串时跳过对应模块
2. `prompt/modules.py`：`build_system_prompt` 改为 `build_system_prompt(instructions: str = "", memory: str = "") -> str`，传参给 `optional_modules`（带默认值：`build_system_prompt()` 与 `build_system_prompt("", "")` 逐字节一致，AC27）
3. 更新调用 `build_system_prompt` 的地方（`agent/__init__.py` 每轮装配），传入 `instruction_text` / `memory_text`；`prompt/__init__.py` 的 `SYSTEM_PROMPT` 兼容别名靠默认值无需改
4. 更新测试：验证非空参数时模块出现在系统提示中，空参数时模块被跳过

**验证：** `pytest tests/test_prompt.py` 通过

## T13: /resume 命令注册**文件：** `src/novacode/tui/commands.py`
**依赖：** 无
**步骤：**
1. 在 `BUILTIN_COMMANDS` 字典中注册 `"/resume" → handle_resume`
2. `handle_resume(ctx)` 函数：调 `await do_resume(ctx)`（`tui/resume.py`）；命令分发阶段 Agent 必不在运行，天然互斥（F46）；无会话或取消时不改变当前会话

**验证：** `python -c "from novacode.tui.commands import BUILTIN_COMMANDS"` 不报错

## T14: 会话列表 UI**文件：** `src/novacode/tui/resume.py`、`src/novacode/tui/run.py`
**依赖：** T5（`list_sessions`）, T13
**步骤：**
1. `run.py` 主循环：
   - `run(cfg, registry, engine, runtime, *, writer, mem_mgr, instruction_text, memory_text, sessions_dir)` 签名扩展，接收 ch09 新增依赖；`root = os.getcwd()`
   - 新增可变容器 `_SessionHolder(conv, writer, ses_ctx)`，主循环与 do_resume 经它读写会话状态；`CommandContext` 新增 `holder` 字段，`conv` 改为只读 property（返回 `holder.conv`），既有 handler 不受影响
   - `/resume` 命令分发 → `handle_resume(ctx)`（内部 `do_resume`）；恢复完成后主循环直接读 `holder.conv` / `holder.writer` / `holder.ses_ctx` 新值
2. `resume.py`：
   - 定义 `SessionItem` 包装 `SessionInfo`，提供 `display_text` 形如 `"<title> · 3 hours ago · model · 1.3KB"`
   - `async do_resume(ctx)`：调 `session.list_sessions(ctx.sessions_dir)` → 用 prompt_toolkit 非全屏 Application 渲染（仿 `_run_approval`）：
     - 上方 `FormattedTextControl` 渲染列表（`cursor` 高亮项），下方 `Buffer` 作为搜索过滤输入框
     - ↑↓ 移动高亮、普通字符进输入框实时过滤（标题子串匹配）、Enter 选择、Esc 取消（返回 None）
   - `async do_resume_session(ctx, info)`：
     - `session.load_session(info.dir)` → `(msgs, last_ts)`；坏行跳过 / 孤立工具调用截断已在 load 内处理
     - 估算 token → 超 `context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN` 则临时 `Conversation.from_messages(msgs)` 后调 `ctx.agent.run_force_compact`（复用 ch08），取压缩后 `msgs`
     - `last_ts` 距当前 > 6h → `msgs.append(Message(role="user", content="[系统提示] 本会话已暂停 <duration>。部分上下文可能已过时，如需最新信息请重新读取相关文件。"))`
     - `session.Writer.open_existing(info.dir)` → `new_writer`
     - `Conversation.from_messages(msgs, new_writer.on_append, new_writer.on_replace)` → `new_conv`
     - `compact.open_session_context(ctx.root, info.id)` → `new_ses_ctx`
     - 替换 `ctx.holder`：`holder.conv = new_conv; holder.writer = new_writer; holder.ses_ctx = new_ses_ctx`；`ctx.runtime.session = new_ses_ctx`
     - 将恢复的 `msgs` 逐条渲染到 `ctx.console`（复用 `user_block` / `assistant_markdown` / `tool_line` / `tool_result_summary`），供用户回看历史；只写终端展示、不重新写入 JSONL（F23）
     - `ctx.console.print("已恢复会话 <id>，共 N 条消息")`

**验证：** `python -c "from novacode.tui.resume import begin_resume"` 不报错

## T15: Agent 记忆更新触发**文件：** `src/novacode/agent/__init__.py`、`src/novacode/agent/runtime.py`
**依赖：** T10（Manager）
**步骤：**
1. `agent/__init__.py`：`Agent.__init__` 新增 `memory_manager: memory.Manager | None = None`、`instruction_text: str = ""`、`memory_text: str = ""` 三个参数，保存为 `self._mem_mgr` / `self._instruction_text` / `self._memory_text`
2. `agent/__init__.py`：`run` 协程 Done 分支（`conv.add_assistant(_ensure_final(summary.text))`）之后：
   - `_extract_recent_turn(conv)` 提取最近一轮消息（从最后一条 user 到当前 assistant）
   - `self.runtime.turn_count += 1`；满足任一条件时 `asyncio.create_task(self._mem_mgr.update_async(recent_msgs))`：① `turn_count % 5 == 0`；② `_has_memory_signal(recent_msgs)` 检测到"记住/记忆/别忘/remember/memo"关键词（忽略大小写）
3. `agent/__init__.py`：`run` 中 `build_system_prompt()` 调用改为 `build_system_prompt(self._instruction_text, self._memory_text)`
4. `agent/__init__.py`：提供 `refresh_memory_text(text: str)` 方法（记忆更新成功落盘后由 Manager 回调调用，F32）
5. `runtime.py`：`SessionRuntime` 新增 `turn_count: int = 0` 字段（跨 run 累计）

**验证：** `pytest tests/test_agent.py` 通过

## T16: cli.py 启动流程串联**文件：** `src/novacode/cli.py`
**依赖：** T1, T2, T3, T4, T10, T12, T14, T15
**步骤：**
1. 在 `config.load(...)` 之后插入：
   - `instructions.Loader(root).load()` → `instruction_text`
   - `memory.Manager(<root>/.novacode/memory, <user_home>/.novacode/memory, provider=None, model="")` → `mem_mgr`
   - `mem_mgr.load_index()` → `memory_text`
2. 在 `new_session_context` 之后：
   - `session.Writer(ses_ctx.session_dir)` → `writer`
3. 在 `new_engine` 之后：
   - `sessions_dir = <root>/.novacode/sessions`；`clean_task = asyncio.create_task(asyncio.to_thread(session.clean_expired, sessions_dir, timedelta(days=30)))`（后台清理，R12）
4. 初始 `Conversation` 由 `run()` 内部构造并注入 `on_append=writer.on_append` / `on_replace=writer.on_replace`（恢复时经 from_messages 重建，cli 不直接构造）
5. 修改 `run(...)` 调用：传入 `writer`、`mem_mgr`、`instruction_text`、`memory_text`、`sessions_dir`
6. `run` 选定 provider 后：`writer.set_model(provider.model)`；`mem_mgr.set_provider(provider, model)`
7. 退出收尾：`await clean_task`；`await mem_mgr.wait_pending()`（等待未完成记忆更新 task），避免 `asyncio.run` 取消未完成任务

**验证：** `python -m novacode` 能启动；`ruff check src/novacode` 无告警；`mypy src/novacode` 无报错（可选）

## T17: 配置示例更新**文件：** `.novacode/config.yaml.example`
**依赖：** 无
**步骤：**
1. 在配置示例中添加注释，说明 novaCODE.md 的加载路径和优先级
2. 说明 `memory` 和 `sessions` 目录的用途

**验证：** 目视检查示例文件内容完整

## 执行顺序

```
T1（Session ID）─┐
T2（Conv 回调）──┤
T3（指令加载）──┤
T9（笔记存储）──┤── 独立基础模块，可并行
T12（Prompt 参数化）─┤
T13（/resume 注册）──┤
T17（配置示例）──────┘

T4（Session Writer）── 依赖 T1
T5（会话列表）──────── 依赖 T1
T6（会话加载）──────── 依赖 T4
T7（会话清理）──────── 依赖 T1
T8（Session 测试）──── 依赖 T4,T5,T6,T7

T10（记忆管理器）──── 依赖 T9
T11（Memory 测试）─── 依赖 T9,T10

T14（会话列表 UI）─── 依赖 T5,T13
T15（Agent 记忆触发）── 依赖 T10,T12
T16（cli.py 串联）─── 依赖 T1,T2,T3,T4,T10,T12,T14,T15
```
~~~