# UX01：流式期间实时反馈 Plan

> 基于已批准的 `spec.md`（F1-F7、N1-N5、AC1-AC8）。

## 架构概览

在现有「provider → agent → TUI」三层流式链路上，新增两条**旁路展示事件流**，与现有正文/工具执行流并行、互不干扰：

- **思考流**：openai provider 捕获 `delta.reasoning_content` 增量 → `StreamEvent.thinking` → agent 转发 `Event.thinking` → TUI 渲染。思考内容**只经 TUI 层临时渲染，绝不经 conversation 层**（F6/N5）。
- **工具参数进度流**：openai provider 把 `delta.tool_calls[].arguments` 的**增量片段**转发为 `StreamEvent.tool_delta` → agent 转发 `Event.tool_delta` → TUI 累计字符数渲染进度（F4）。完整 ToolCall 拼接与执行路径保持现状（F5）。

TUI 层引入「阶段状态机」（thinking / generating / text / tools），决定 Live 动态区显示什么；Ctrl+O（0x0f）经 `_CancelWatcher` 扩展捕获，切换思考视图展开/折叠。

## 核心数据结构

### `ToolDelta`（新增，`llm/__init__.py`）
```python
@dataclass
class ToolDelta:
    """一次工具参数增量片段（仅展示用，不参与执行）。"""
    index: int      # openai delta.tool_calls[i].index，多工具并发时区分
    name: str       # 工具名；仅首段增量携带（delta.function.name）
    args: str       # 本次新增的 arguments 片段（增量，非累计）
```

### `StreamEvent`（扩展，`llm/__init__.py`）
```python
@dataclass
class StreamEvent:
    text: str = ""
    thinking: str = ""                       # 新增：思考内容增量（reasoning_content）
    tool_delta: list[ToolDelta] = ...        # 新增：工具参数增量片段（0 或多个）
    tool_calls: list[ToolCall] = ...         # 不变：流结束后的完整工具调用
    usage: Usage | None = None
    done: bool = False
    err: Exception | None = None
```

### `Usage`（扩展，`llm/__init__.py` 与 `agent/__init__.py` 两处同步）
```python
# llm.Usage 新增：
reasoning_tokens: int = 0   # 本轮思考 token（openai: completion_tokens_details.reasoning_tokens）

# agent.Usage 新增：
reasoning: int = 0          # 透传 llm.Usage.reasoning_tokens
```

### `Event`（扩展，`agent/__init__.py`）
```python
@dataclass
class Event:
    text: str = ""
    thinking: str = ""                      # 新增：思考内容增量（旁路展示，不入历史）
    tool_delta: list[ToolDelta] = ...       # 新增：工具参数增量（旁路展示）
    tool: ToolEvent | None = None
    usage: Usage | None = None
    ...   # 其余字段不变
```

### `_ThinkingView`（新增，`tui/thinking.py`）
```python
@dataclass
class _ThinkingView:
    """思考渲染状态机：滚动最近 N 行 / 展开全文 / 让位。"""
    max_lines: int = 3      # 滚动态保留行数（spec N=3）
    expanded: bool = False  # Ctrl+O 展开态

    def feed(self, chunk: str) -> None:   # 增量 → 拆行入 deque(_recent) 与 list(_full)
    def render(self) -> Text | None:       # expanded ? 全文 : 最近 N 行；空则 None
    def toggle(self) -> None:              # 切换展开/滚动
    def clear(self) -> None:               # 让位时清空（正文/工具增量到达）
```

### `_ToolProgress`（新增，`tui/thinking.py`）
```python
@dataclass
class _ToolProgress:
    """工具参数生成进度：按 index 聚合 tool_delta。"""
    by_index: dict[int, ToolDelta]   # 每工具聚合 name + 累计字符数
    def feed(self, d: ToolDelta) -> None
    def render(self) -> Text | None  # "⚙ 正在生成 write_file 参数…(已 12.3K 字符)"
```

### `_Phase`（新增，`tui/run.py`）
```python
class _Phase(Enum):
    THINKING = "thinking"    # 推理段：思考视图
    GENERATING = "generating"  # 生成段：工具参数进度
    TEXT = "text"            # 正文：打字机
    TOOLS = "tools"          # 工具执行：Running 列表
    IDLE = "idle"            # 兜底：思考中计时
```

## 模块设计

### `llm/__init__.py`（修改）
**职责：** 承载协议无关类型。新增 `ToolDelta`；扩展 `StreamEvent`（`thinking`/`tool_delta`）与 `Usage`（`reasoning_tokens`）。anthropic 适配器字段默认值兼容，不被迫改动。

### `llm/openai_provider.py`（修改）
**职责：** 捕获并转发思考增量、工具参数增量、reasoning 用量。
- `delta.reasoning_content` → `yield StreamEvent(thinking=...)`（逐段转发，非拼完）。
- 现有 `tool_calls_buf` 拼接逻辑**保留**（仍产出完整 `tool_calls`）；在拼接 `arguments` 的同一处，对每个增量片段 `yield StreamEvent(tool_delta=[ToolDelta(index, name, args_delta)])`（name 取 `delta.function.name` 非空时）。
- usage chunk 解析补 `reasoning_tokens`：`getattr(chunk.usage.completion_tokens_details, "reasoning_tokens", 0)`，DeepSeek 无该字段时默认 0（OpenAI 标准端点可能没有）。

### `llm/anthropic_provider.py`（不改）
**理由：** spec「不做」——anthropic 的 thinking_delta 与回灌签名问题留后续。新增字段默认值兼容现有代码。

### `agent/__init__.py`（修改）
**职责：** 把旁路事件透传 TUI，但不写入对话状态。
- `_request`：`se.thinking` → `yield Event(thinking=se.thinking)`（**不 append parts**）；`se.tool_delta` → `yield Event(tool_delta=se.tool_delta)`（**不改变 calls**）；`se.usage` 透传时带上 `reasoning`。
- `_Summary` 与 `Event.usage` 使用扩展后的 agent `Usage`。

### `tui/thinking.py`（新增）
**职责：** 思考视图 + 工具进度渲染（纯渲染，无 I/O）。`_ThinkingView`、`_ToolProgress` 两个 dataclass。`_fmt_char_count` 复用 `_fmt_tok` 的 k 缩写格式（12.3K）。

### `tui/run.py`（修改）
**职责：** 阶段状态机 + 渲染分派 + Ctrl+O + 完成统计。
- `_stream_reply`：新增 `thinking_view = _ThinkingView()`、`tool_progress = _ToolProgress()`、`phase`。事件分派：
  - `ev.thinking` → `thinking_view.feed`，phase=THINKING（若当前在 TEXT/TOOLS 则忽略，思考已让位）。
  - `ev.tool_delta` → `tool_progress.feed`，phase=GENERATING；同时 `thinking_view.clear()`（F3 让位）。
  - `ev.text` → phase=TEXT；`thinking_view.clear()`。
  - `ev.tool START` → phase=TOOLS；`thinking_view.clear()`。
  - `ev.usage` → 累加本轮 `reasoning`。
  - 完成：扩展 `· 用时 Xs` 为 `· 用时 Xs · 思考 N tokens`（`_fmt_tok` 或全称 tokens 展示，spec F7）。
- `_update_dynamic` 按 `phase` 分派渲染：THINKING→思考视图（展开优先）；GENERATING→工具进度；TEXT→打字机尾部；TOOLS→Running 列表；IDLE→思考中计时。
- `_ticker`：仅 IDLE 阶段显示「思考中…(Ns)」；THINKING 阶段思考视图自带内容，不显示计时。
- `_CancelWatcher` 扩展：新增 `expand: asyncio.Event`；Linux 半 raw 读 `b"\x0f"`、Windows `getwch()` 读 `'\x0f'` 时 set `expand`（不取消）。`_stream_reply` 每轮检查 `expand.is_set()` → `thinking_view.toggle()` 并 reset。

## 模块交互（数据流）

```
openai_provider.stream()
  │ reasoning_content 增量 ──▶ StreamEvent(thinking=…)
  │ content 增量 ──────────▶ StreamEvent(text=…)
  │ tool_calls.arguments 增量 ─▶ StreamEvent(tool_delta=[…])
  │ 流结束 ────────────────▶ StreamEvent(tool_calls=[完整])   [执行路径，不变]
  │ usage chunk ───────────▶ StreamEvent(usage=Usage(reasoning_tokens=N))

agent._request()
  │ se.thinking ───────────▶ yield Event(thinking=…)         [旁路，不进 parts]
  │ se.tool_delta ─────────▶ yield Event(tool_delta=…)       [旁路，不进 calls]
  │ se.text ───────────────▶ parts.append + Event(text=…)
  │ 结束 ──────────────────▶ _Summary(text, calls, usage, err)

tui._stream_reply()
  │ ev.thinking  → thinking_view.feed → phase=THINKING
  │ ev.tool_delta→ tool_progress.feed + thinking_view.clear() → phase=GENERATING
  │ ev.text      → renderer.feed + thinking_view.clear() → phase=TEXT
  │ ev.tool      → Running 列表 → phase=TOOLS
  │ 完成         → 打印「· 用时 Xs · 思考 N tokens」（reasoning 累加值）
```

## 文件组织

```
src/novacode/
├── llm/
│   ├── __init__.py          — 修改：ToolDelta、StreamEvent.thinking/tool_delta、Usage.reasoning_tokens
│   ├── openai_provider.py   — 修改：思考增量转发、工具增量转发、reasoning 用量解析
│   └── anthropic_provider.py — 不改（默认值兼容）
├── agent/__init__.py        — 修改：Event.thinking/tool_delta、Usage.reasoning、_request 旁路转发
└── tui/
    ├── run.py               — 修改：_Phase 状态机、_update_dynamic 分派、_CancelWatcher 扩展、完成统计行
    └── thinking.py          — 新增：_ThinkingView、_ToolProgress、字符数格式化
docs/ux01/
├── spec.md                  — 已批准
└── plan.md                  — 本文件
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 思考/增量如何传输 | `StreamEvent.thinking` / `tool_delta` 作为**旁路事件**，agent 只转发不落 conversation | 满足 F6/N5：不进历史/JSONL，resume 零影响 |
| 工具增量传「增量片段」而非累计全文 | `ToolDelta.args` 只带本次新增片段，TUI 层累计字符数 | 20KB 参数若每次带累计全文，事件体积 O(n²)，浪费 |
| 思考渲染位置 | 独立 `tui/thinking.py`（`_ThinkingView`/`_ToolProgress`），run.py 只做状态分派 | run.py 已 800 行；渲染逻辑自包含可单测 |
| 滚动 N 行数据结构 | 双缓冲：`deque(maxlen=N)` 存最近 N 行 + `list` 存全文 | 滚动 O(1)，展开直接读全文，避免 O(n²) 拼接（N4） |
| Ctrl+O 捕获 | 扩展 `_CancelWatcher` 监听 0x0f，set 独立 `expand` Event | 复用现有半 raw 读键机制，不新增并行终端监听（N3） |
| 完成统计的思考 token | `Usage.reasoning_tokens`（真实值），非估算 | 流结束才可拿，F7 定位「完成后统计」；流式期间用字符数 |
| 展示措辞 | 新增展示统一 **tokens** 全称（`· 思考 N tokens`） | 用户要求；现有状态栏 `tok` 保持现状不在本次范围 |
| 阶段让位 | 进入 TEXT/GENERATING/TOOLS 时 `thinking_view.clear()` | F3：思考不写 scrollback、正文接管 |

## spec 覆盖自查

| spec 需求 | 架构归属 |
|-----------|---------|
| F1 思考实时滚动 | `_ThinkingView`（deque 最近 N 行）+ phase=THINKING 渲染 |
| F2 Ctrl+O 展开/折叠 | `_CancelWatcher.expand` + `_ThinkingView.toggle` |
| F3 阶段让位 | `thinking_view.clear()` 于 TEXT/GENERATING/TOOLS 分派处 |
| F4 生成段字符进度 | `tool_delta` 旁路流 + `_ToolProgress` |
| F5 工具执行不受影响 | 完整 `tool_calls` 拼接/执行路径不变 |
| F6 思考不进持久化 | 旁路事件不经 conversation 层（`_request` 不 append parts） |
| F7 完成后本轮思考统计 | `Usage.reasoning_tokens` → 用时行扩展 |
| N3 平台兼容 | `_CancelWatcher` Windows/Linux 双分支补 0x0f |
| N4 性能 | deque 双缓冲 |
| N5 resume 兼容 | 旁路事件不落盘，JSONL 不变 |

无缺口。
