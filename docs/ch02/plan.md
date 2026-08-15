~~~Markdown
# 多协议 LLM 终端对话客户端 Plan## 技术栈
- 语言：Python 3.12+
- 终端交互：[prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/)（跨平台，底部
  「上横线 + `❯` 输入行 + 下横线 + 状态栏」输入框、Enter 提交 / Ctrl+J 换行、Ctrl+C 退出）+
  [Rich](https://rich.readthedocs.io/)（Live 流式打字机 + Markdown/语法高亮增量渲染）。
  对话输出**直接打印进终端 scrollback**，与命令行融为一体（Claude Code 风格），用终端滚轮/滚动条回看历史。
- 配置：YAML 解析（`pyyaml`，import 名 `yaml`）
- LLM 通信：官方 Python SDK —— `anthropic`（`AsyncAnthropic`）、`openai`（`AsyncOpenAI`），均原生支持
  async 流式（SDK 内部已处理 SSE）

## 架构概览（分层）
1. 入口层 `novacode.cli` —— 加载配置、错误处理、`asyncio.run(tui.run(cfg))`。
2. 配置层 `novacode.config` —— 读取并校验 `.novacode/config.yaml`，给出 providers 列表。
3. LLM 协议层 `novacode.llm` —— 定义协议无关的 `Provider` Protocol 与统一消息/流式事件类型；
   anthropic、openai 两个适配器各自封装官方 SDK、统一吐出文本增量（思考增量内部丢弃）。
4. 会话层 `novacode.conversation` —— 进程内维护多轮历史，提供完整上下文。
5. 提示词/资源 `novacode.prompt` —— 内置 system prompt 与启动 banner（大写 NOVACODE 艺术字）。
6. 终端层 `novacode.tui` —— 分层借鉴 ForgeCode：**输出层 Rich**（`console.print` 直写终端
   scrollback + `Live` 流式打字机 + `Markdown`/`Syntax` 增量渲染）、**输入层 prompt_toolkit**
   （每轮轻量非全屏 Application：上横线 + `❯` 输入行 + 下横线 + 状态栏，尾随光标处）、
   `select.py` 多 provider 选择、`view.py` 渲染拼装。

## 数据流（一轮对话）
`_input_frame()` 渲染输入框（Enter 提交/Ctrl+J 换行）→ conversation 追加 user 消息 →
`_stream_reply()`：`Live` 显示「思考中… (Ns)」计时；`_StreamMarkdown` 增量渲染 ——
已完成的行经 `live.console.print` 提交进 scrollback（Markdown 语法 + 代码块 pygments 高亮），
未完成的当前行在 Live 区域原地更新（打字机）→ 结束后显示总耗时 →
conversation 追加 assistant 消息 → 回到输入框。

> 备注：输入框是每轮一个 `prompt_toolkit.Application(full_screen=False)`，提交后整框上滚成为
> 「用户回合」历史；`Provider.stream` 是 async generator，`async for` 直接驱动流式渲染，
> 无 widget/DOM 胶水层。

## 核心数据结构与接口

```python
# ───────── config 层 ─────────
@dataclass
class ProviderConfig:
    name: str                          # 状态栏/提示行左侧显示
    protocol: Literal["anthropic", "openai"]
    api_key: str
    model: str
    base_url: str | None = None        # None 则用 SDK 默认端点
    thinking: bool = False             # 仅 anthropic 生效

@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)

def load(path: str) -> Config: ...     # 加载 + 校验

# ───────── llm 层（协议无关）─────────
@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str

@dataclass
class StreamEvent:
    text: str = ""                     # 文本增量
    done: bool = False                 # 本轮正常结束
    err: Exception | None = None       # 出错（与 done 互斥）

class Provider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]: ...

def new_provider(cfg: ProviderConfig) -> Provider: ...

# ───────── conversation 层 ─────────
class Conversation:
    def add_user(self, text: str) -> None: ...
    def add_assistant(self, text: str) -> None: ...
    def messages(self) -> list[Message]: ...

# ───────── prompt 层 ─────────
SYSTEM_PROMPT: str = "..."
NOVACODE_BANNER: str = "..."           # 大写 NOVACODE 的 ASCII 艺术字
def render_banner(version: str, cwd: str) -> str: ...

# ───────── tui 层 ─────────
# run.py
async def run(cfg: Config) -> int: ...
#   - 打印 banner（一次）、选 provider（单份直进 / 多份 choose_provider）
#   - 循环：_input_frame(provider) → _stream_reply(...)；/exit 或 Ctrl+C 退出
async def _input_frame(provider) -> str | None: ...
#   - 每轮轻量非全屏 Application：上横线 + ❯ 输入行 + 下横线 + 状态栏，尾随光标
#   - kb：enter→提交、c-j→换行、c-c→退出；返回 None 表示退出（/exit/Ctrl+C）
class _StreamMarkdown: ...
#   - 增量 markdown 渲染：已完成的行经 live.console.print 提交进 scrollback，
#     未完成的当前行在 Live 区域原地更新（打字机）；代码块逐行 pygments 高亮
async def _stream_reply(provider, conv, console) -> None: ...
#   - Live「思考中…(Ns)」→ 增量渲染 → 显示总耗时 → conv.add_assistant
# select.py
def choose_provider(providers) -> ProviderConfig: ...
# view.py
def user_block(text) -> Text: ...
def error_block(err) -> Text: ...      # 红色
def assistant_markdown(reply) -> Group: ...
```

## 模块设计### 模块 `novacode.config`
同前：`load(path) -> Config`，校验规则不变，失败抛 `ConfigError`。

### 模块 `novacode.llm`
同前：`Provider` Protocol、`Message`、`StreamEvent`、`new_provider`；两适配器封装官方 SDK。

### 模块 `novacode.conversation`
同前：进程内多轮历史。

### 模块 `novacode.prompt`
同前：`SYSTEM_PROMPT`、`NOVACODE_BANNER`、`render_banner`。

### 模块 `novacode.tui`
职责：prompt_toolkit + Rich 主循环，承载输入/流式输出/选择/错误交互。
- `run.py`：`run(cfg)` 主循环；`_input_frame()` 每轮轻量输入框（横线包围 + 状态栏）；
  `_stream_reply()` + `_StreamMarkdown` 增量渲染进 scrollback（Rich Live 打字机 +
  Markdown/pygments）；键绑定 Enter 提交、Ctrl+J 换行、Ctrl+C 退出；`/exit` 识别。
- `select.py`：多 provider 时打印列表 + 输入序号/名称选择。
- `view.py`：`user_block` / `error_block`（红色）/ `assistant_markdown`（rich markdown 拼装）。
依赖：`prompt_toolkit`、`rich`、本项目 `llm`、`conversation`、`config`、`prompt`。

### 模块 `novacode.cli`（入口）
`config.load(...)` → 出错打印可读信息并 `sys.exit(1)` → `asyncio.run(tui.run(cfg))`。
退出路径：`/exit` 或 Ctrl+C → 循环 break，prompt_toolkit 还原终端（N7）。

## 模块交互### 调用链（启动）
```
main() → config.load(".novacode/config.yaml")
       → 若 ConfigError：打印可读错误、sys.exit(1)
       → asyncio.run(tui.run(cfg))
           → console.print 打印 banner（NOVACODE 艺术字 + 版本 + cwd + 就绪行）一次
           → len(providers)==1：直接用 providers[0]；>1：choose_provider()
           → new_provider(cfg) 构造 provider
           → 循环：_input_frame(provider) → _stream_reply(provider, conv, console)
```

### 时序（一轮对话，核心）
```
_input_frame() 渲染输入框（上横线 + ❯ 输入 + 下横线 + 状态栏），返回 text
  → text 为 None（/exit 或 Ctrl+C）→ break（退出）
  → conv.add_user(text)
  → _stream_reply(provider, conv, console):
      Live 初始显示 "思考中… (0s)"，create_task(_ticker) 每秒更新秒数
      async for ev in provider.stream(conv.messages()):
        - ev.err  → 停 ticker，红色 error_block（程序不退出）
        - ev.text → 停 ticker；renderer.feed(text)：
                     已完成的行 → live.console.print（Markdown/Syntax 增量渲染进 scrollback）
                     未完成行   → live.update(tail) 原地打字机更新
        - ev.done → break
      → renderer.flush() 提交尾部；Live 收尾
      → 正常：打印 "· 用时 X.Xs"，conv.add_assistant(reply)
  → 回到 _input_frame()
```

### 时序（退出）
```
输入 "/exit" 或 Ctrl+C
  → run 循环 break → asyncio.run 结束 → prompt_toolkit 还原终端（N7）
```

### 数据流图
```
config.yaml ──load──> list[ProviderConfig] ──new_provider──> Provider
用户输入 ──_input_frame──> conversation(+user) ──messages()──> Provider.stream
Provider.stream ──async generator──> StreamEvent (text / done / err)
                                         │
                                         └──async for──> tui._stream_reply
                                                            ├── text  → _StreamMarkdown 增量渲染
                                                            │            └── live.console.print 进 scrollback
                                                            └── done  → 尾部提交 + 总耗时
                                                                       → conversation(+assistant)
```

## 文件组织
```
novacode/
├── pyproject.toml                  — PEP 621 项目元数据、依赖、脚本入口
├── README.md
├── .novacode/
│   └── config.yaml                 — 运行配置（providers 列表）；附 config.yaml.example
├── src/
│   └── novacode/
│       ├── __init__.py
│       ├── __main__.py             — 允许 `python -m novacode`
│       ├── cli.py                  — 入口：加载配置、错误处理、启动主循环
│       ├── config.py               — Config / ProviderConfig 类型、load 与校验
│       ├── prompt.py               — SYSTEM_PROMPT、NOVACODE_BANNER、render_banner
│       ├── conversation.py         — 单会话多轮历史
│       ├── llm/
│       │   ├── __init__.py         — Provider Protocol、Message、StreamEvent、new_provider 工厂
│       │   ├── anthropic_provider.py  — anthropic 适配器（封装 AsyncAnthropic）
│       │   └── openai_provider.py     — openai 适配器（封装 AsyncOpenAI）
│       └── tui/
│           ├── __init__.py
│           ├── run.py              — 主循环（prompt_toolkit 输入 + Rich 流式输出）
│           ├── select.py           — provider 选择
│           └── view.py             — 渲染拼装、错误样式、markdown 定型
└── tests/
    ├── test_config.py
    ├── test_conversation.py
    └── test_tui.py                 — view 渲染 + _stream 流式/错误
```
说明：
- 依赖：`prompt_toolkit`、`rich`、`anthropic`、`openai`、`pyyaml`。
- `.novacode/config.yaml` 含真实密钥，应在 `.gitignore` 忽略；提交 `config.yaml.example`。
- `pyproject.toml` 里通过 `[project.scripts] novacode = "novacode.cli:main"` 暴露 CLI 入口。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 语言 | Python 3.12+ | 项目既定 |
| 终端交互 | prompt_toolkit + Rich | 对话输出直接进终端 scrollback，与命令行融为一体（Claude Code 风格）；跨平台（Windows/Linux 一致） |
| markdown 渲染 | Rich 的 `rich.markdown.Markdown` | 代码块语法高亮、列表、强调齐全；随终端宽度自适应 |
| LLM 通信 | 官方 Python SDK（`anthropic` / `openai`） | SDK 内置 SSE 解析与 async 流 |
| 协议抽象 | 统一 `Provider` Protocol + 两适配器 | 满足 F3/N3；上层不感知协议 |
| 输入行 | 每轮轻量 `Application(full_screen=False)`：透明 spacer 占满剩余高度 + 上横线 + ❯ 输入 + 下横线 + 状态栏 | 输入框盒子尾随光标处（ForgeCode 同款）；透明 spacer 使重绘覆盖整屏，resize 不残留旧边框；Enter 提交、Ctrl+J 换行、Ctrl+C 退出 |
| 流式渲染 | Rich `Live` 打字机 + `Markdown`/`Syntax`(pygments) 增量渲染，已完成的行提交进 scrollback | 逐字实时呈现且语法高亮，无需结束后二次定型（F8） |
| 渲染模型 | 输出直接打印进终端 scrollback，无内部滚动区 | 历史用终端滚轮/滚动条回看（N1/N6） |
| 计时 | `time.monotonic()` + `Live.update` 显示 "思考中… (Ns)"，结束后显示总耗时 | F12 |
| provider 选择 | 单份直进 / 多份打印列表 + 输入选择 | 满足 F2 |
| 历史 | 进程内 `list[Message]`，单会话 | 满足 F6；不持久化 |
| system prompt | 内置常量，适配器注入 | 满足 F4 |
| 配置 | `.novacode/config.yaml` + `pyyaml`；密钥入 `.gitignore` | N5 密钥安全 |
| 错误处理 | 运行时错误转红色 `error_block`，不退出 | 满足 F11 |
| 类型/质量 | `typing.Protocol` + `dataclass`；`ruff format` + `ruff check` | 简洁，无运行时依赖 |
~~~
