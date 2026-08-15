# UX01：流式期间实时反馈 Tasks

> 基于已批准 spec.md（F1-F7）与 plan.md。执行顺序见末尾。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/novacode/llm/__init__.py` | `ToolDelta`、`StreamEvent.thinking/tool_delta`、`Usage.reasoning_tokens` |
| 修改 | `src/novacode/llm/openai_provider.py` | 思考增量、工具增量、reasoning 用量解析 |
| 修改 | `src/novacode/agent/__init__.py` | `Event.thinking/tool_delta`、`Usage.reasoning`、`_request` 旁路转发 |
| 新增 | `src/novacode/tui/thinking.py` | `_ThinkingView`、`_ToolProgress`、字符数格式化 |
| 修改 | `src/novacode/tui/run.py` | `_Phase` 状态机、`_update_dynamic` 分派、`_CancelWatcher` 扩展、完成统计行 |
| 修改 | `tests/test_llm.py` | `FakeDelta` 扩展 + openai 思考/增量/用量测试 |
| 新增 | `tests/test_tui_thinking.py` | `_ThinkingView`/`_ToolProgress` 纯渲染单测 |
| 修改 | `tests/test_tui.py` | `_stream_reply` 新事件与完成统计测试 |

## T1: llm 基础类型扩展

**文件：** `src/novacode/llm/__init__.py`
**依赖：** 无
**步骤：**
1. 新增 `@dataclass class ToolDelta`：`index: int`、`name: str = ""`、`args: str = ""`。
2. `StreamEvent` 新增字段：`thinking: str = ""`、`tool_delta: list[ToolDelta] = field(default_factory=list)`。
3. `Usage` 新增字段：`reasoning_tokens: int = 0`。

**验证：** `.venv/Scripts/python.exe -c "from novacode.llm import ToolDelta, StreamEvent, Usage; e=StreamEvent(thinking='x'); assert e.thinking=='x'; assert Usage().reasoning_tokens==0"` 无输出即为通过。

## T2: openai 思考增量转发

**文件：** `src/novacode/llm/openai_provider.py`、`tests/test_llm.py`
**依赖：** T1
**步骤：**
1. 流循环内、处理 `delta.content` 的同级位置：`reasoning = getattr(delta, "reasoning_content", None)`，非空则 `yield StreamEvent(thinking=reasoning)`（逐段转发，不拼装）。
2. `tests/test_llm.py` 的 `FakeDelta` 加 `reasoning_content=None` 参数；新增测试：造一个带 `reasoning_content` 的 chunk，断言 `stream` 吐出 `StreamEvent(thinking=...)`，且正文/usage 不受影响。

**验证：** `.venv/Scripts/python.exe -m pytest tests/test_llm.py -q` 新增用例通过，原有用例不回归。

## T3: openai 工具增量转发 + reasoning 用量

**文件：** `src/novacode/llm/openai_provider.py`、`tests/test_llm.py`
**依赖：** T1
**步骤：**
1. 现有 `tool_calls_buf` 拼接 `arguments` 的循环内，对每个 `arguments` 增量片段同步 `yield StreamEvent(tool_delta=[ToolDelta(index=tc.index, name=(tc.function.name or ""), args=tc.function.arguments)])`（name 仅首个 delta 非空，如实透传）。
2. usage chunk 解析处：`details2 = getattr(chunk.usage, "completion_tokens_details", None)`；`reasoning = getattr(details2, "reasoning_tokens", 0) or 0`，填入 `Usage.reasoning_tokens`。
3. 测试：`FakeUsage` 加可选 `completion_tokens_details`；新增用例——分片 arguments 断言 `tool_delta` 逐段携带增量（含 index/name）；usage 带 `reasoning_tokens` 断言透传；同时断言流末仍产出完整 `tool_calls`（F5）。

**验证：** `.venv/Scripts/python.exe -m pytest tests/test_llm.py -q` 通过。

## T4: agent 旁路转发

**文件：** `src/novacode/agent/__init__.py`
**依赖：** T1
**步骤：**
1. `agent.Usage` 新增 `reasoning: int = 0`（import 处 `llm.ToolDelta` 一并引入）。
2. `Event` 新增：`thinking: str = ""`、`tool_delta: list[ToolDelta] = field(default_factory=list)`。
3. `_request` 内：`if se.thinking: yield Event(thinking=se.thinking)`（**不 append parts**）；`if se.tool_delta: yield Event(tool_delta=se.tool_delta)`（**不改变 calls**）；`usage` 组装时 `reasoning=se.usage.reasoning_tokens`。
4. 测试（`tests/test_tui.py` 或新增）：用脚本化 provider 吐 thinking/tool_delta 事件，断言 `agent.run` 的 Event 流透传 thinking 与 tool_delta，且 conversation 消息数/内容不含思考（F6）。

**验证：** `.venv/Scripts/python.exe -m pytest tests/ -q` 中 agent 相关用例通过。

## T5: tui/thinking.py 渲染模块

**文件：** `src/novacode/tui/thinking.py`（新增）、`tests/test_tui_thinking.py`（新增）
**依赖：** 无
**步骤：**
1. `_ThinkingView(max_lines=3)`：
   - 双缓冲：`self._recent: deque[str] = deque(maxlen=max_lines)`、`self._full: list[str] = []`、`self.expanded = False`。
   - `feed(chunk)`：按 `\n` 拆行，整行入 `_full` 与 `_recent`；不完整尾部行存 `self._partial`，下次 feed 续接。
   - `render()`：`expanded` → 全文（`"\n".join(_full)`，尾部拼 partial）；否则最近 N 行；空 → None。
   - `toggle()`、`clear()`。
2. `_ToolProgress`：`by_index: dict[int, ToolDelta]`；`feed(d)` 聚合 name（非空覆盖）+ 累计字符数；`render()` → `Text("⚙ 正在生成 {name} 参数…(已 {fmt} 字符)")`。
3. 字符格式化 `fmt_char_count(n)`：≥1000 → `12.3K`（复用 run.py `_fmt_tok` 的缩写风格，独立实现避免循环导入）。
4. 单测覆盖：feed 换行拆分/partial 续接、滚动只留 3 行、toggle 切换、clear 清空、多 index 工具进度聚合与字符数累计。

**验证：** `.venv/Scripts/python.exe -m pytest tests/test_tui_thinking.py -q` 全绿。

## T6: _CancelWatcher 扩展 Ctrl+O

**文件：** `src/novacode/tui/run.py`
**依赖：** T5
**步骤：**
1. `_CancelWatcher.__init__(self, cancel, expand=None)`：`self._expand = expand or asyncio.Event()`；提供 `expand` property。
2. `_start_linux` 的 `_on_key`：`b"\x0f"` → `self._expand.set()` 并 `_teardown_linux()`（与 Esc/Ctrl+C 同样只响应一次）；`b"\x03"`/`b"\x1b"` → 原有 cancel 逻辑。
3. `_start_windows` 的 `_watch`：`ch in ("\x1b", "\x03")` → cancel；`ch == "\x0f"` → expand.set() 后 break。

**验证：** 编译通过；`pytest tests/test_tui.py -q` 现有取消用例不回归；Ctrl+O 实际行为留 T10 端到端。

## T7: run.py 阶段状态机 + 渲染分派

**文件：** `src/novacode/tui/run.py`
**依赖：** T4、T5、T6
**步骤：**
1. 新增 `class _Phase(Enum): THINKING/GENERATING/TEXT/TOOLS/IDLE`。
2. `_stream_reply`：初始化 `thinking_view = _ThinkingView()`、`tool_progress = _ToolProgress()`、`phase = _Phase.IDLE`。事件分派：
   - `ev.thinking`：`thinking_view.feed`；`phase = THINKING`（仅当 phase 在 IDLE/THINKING 时，已让位则忽略）。
   - `ev.tool_delta`：`tool_progress.feed`；`thinking_view.clear()`；`phase = GENERATING`。
   - `ev.text`：`thinking_view.clear()`；`phase = TEXT`。
   - `ev.tool START`：`thinking_view.clear()`；`phase = TOOLS`。
   - `ev.tool END`：保持 TOOLS 直到 cur_tools 空后回 IDLE。
   - 每轮分派后 `_update_dynamic`。
3. `_update_dynamic` 按 phase 渲染：THINKING → `thinking_view.render()`（优先 expanded 全文）；GENERATING → `tool_progress.render()`；TEXT → `renderer._tail()`；TOOLS → Running 列表；IDLE → 思考中计时。
4. `_ticker` 只在 IDLE 阶段更新「思考中…(Ns)」；非 IDLE 不覆盖 Live。
5. Ctrl+O 消费：循环内检查 `watcher.expand.is_set()` → `thinking_view.toggle()`、`expand.clear()`、`_update_dynamic`。
6. 测试：`tests/test_tui.py` 用 `_ScriptProvider` 依次吐 thinking→tool_delta→text 事件，断言 `FakeLive` 的 update 内容依次为思考文本 / 工具进度 / 正文；现有 `test_stream_reply_*` 不回归。

**验证：** `.venv/Scripts/python.exe -m pytest tests/test_tui.py -q` 新增与原有全绿。

## T8: 完成统计行（思考 tokens）

**文件：** `src/novacode/tui/run.py`
**依赖：** T4
**步骤：**
1. `_stream_reply` 累计 `reasoning_total += ev.usage.reasoning`（在 `ev.usage` 处理处）。
2. 完成打印（`else` 分支）：`· 用时 {s}s · 思考 {n} tokens`（n>0 才附思考段；用 **tokens** 全称；`_fmt_tok` 缩写数值）。
3. 测试：脚本 provider 带 `Usage(reasoning=436)`，断言 console 输出含 `思考 436 tokens`；无 reasoning 时不输出思考段。

**验证：** `.venv/Scripts/python.exe -m pytest tests/test_tui.py -q` 通过。

## T9: 全量测试 + lint

**文件：** 全局
**依赖：** T1-T8
**步骤：**
1. `.venv/Scripts/python.exe -m pytest -q` —— 全部通过（原 417 + 新增）。
2. `.venv/Scripts/python.exe -m ruff check src/novacode tests && .venv/Scripts/python.exe -m ruff format src/novacode tests`。

**验证：** 两条命令输出无错误。

## T10: tmux 端到端验收

**文件：** 无（运行验证）
**依赖：** T9
**步骤：**
1. WSL tmux 启动 NovaCode（真实 deepseek-v4-flash），按 checklist.md 场景逐项验证：长思考任务 → 思考内容滚动、Ctrl+O 展开全文、再按恢复；生成大工具参数 → 字符进度增长；完成后「· 用时 Xs · 思考 N tokens」；`/resume` 恢复后对话正常。
2. Windows 本机跑一次真实对话复核 Ctrl+O（msvcrt 路径）。

**验证：** 对照 `docs/ux01/checklist.md`（阶段四产出）逐项记录证据。

## 执行顺序

```
T1（类型层）──────────────────┐
  ├→ T2 openai 思考增量 ──────┤
  ├→ T3 openai 工具增量/用量 ──┤→ T4 agent 旁路转发 ──┐
T5（thinking.py 纯渲染）─────→ T6（Ctrl+O 捕获）─────┼→ T7（状态机/渲染分派）┐
                                     T4 → T8（完成统计）──────────────┤
                                                            T9（全量+lint）→ T10（tmux 验收）
```

依赖：T2/T3/T4 依赖 T1；T6 依赖 T5；T7 依赖 T4+T5+T6；T8 依赖 T4；T9 依赖 T7+T8；T10 依赖 T9。
T5 纯渲染、无依赖，可最早开始；T2/T3 可并行。
