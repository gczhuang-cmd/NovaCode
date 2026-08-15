```Markdown
# 多协议 LLM 终端对话客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式。

## 实现完整性
- [x] 配置加载：合法 `.novacode/config.yaml` 能解析出 providers 列表（验证：单测 + 启动进入对话）。(AC1/F1)
- [x] 配置校验：缺密钥/非法 protocol/文件缺失时给出可读错误并非零退出，无未捕获堆栈（验证：删字段/改 protocol/删文件分别运行 `python -m novacode`）。(AC1/N4)
- [x] 单 provider 直进：仅一条配置时启动直接进入对话（验证：单条配置运行）。(AC2/F2)
- [x] 多 provider 选择：多条配置时出现方向键 `OptionList`，选定后进入对话（验证：两条配置运行、上下选择 + Enter）。(AC2/F2)
- [x] 内置 system prompt 与历史随请求发送（验证：问"你的角色/规则"，回答体现内置 prompt；多轮见 AC6）。(AC4/F4)
- [ ] thinking：anthropic 配 `thinking: true` 时启用，且界面不出现任何思考文本（验证：开启后观察仅最终回复）。(AC5/F5)
  > 适配器已按 `thinking={"type":"enabled","budget_tokens":2048}` 实现、思考增量接收即丢弃；因暂无 anthropic 真实密钥，未做端到端联调。
- [x] 流式逐字：回复以 Rich Live 打字机逐字实时呈现，已完成的行即时渲染进 scrollback（验证：长回复肉眼可见逐步输出且可滚轮回看）。(AC5/F8)
- [x] markdown 增量渲染：流式期间 Markdown 语法 + 代码块 pygments 高亮增量渲染，无需结束后二次定型（代码块/列表/强调正确、不重复不截断）（验证：让模型输出含代码块与列表的内容，滚轮回看无重复）。(AC8/F8)
- [x] 多行输入：Ctrl+J 换行、Enter 提交、提交后输入框清空（验证：输入两行后提交）。(AC9/F9)
- [x] 响应计时：自提交即显示 `Imagining… (Ns)` 且秒数递增，结束后显示总耗时（验证：发一条慢回复观察）。(AC12/F12)
- [x] 错误反馈：错误 key/不存在模型时，错误在对话区可区分样式（红色）显示且不退出（验证：改坏 key 运行后再正常发一条）。(AC11/F11)
- [x] 退出：`/exit` 与 Ctrl+C 均能安全退出，终端恢复正常（验证：两种方式各试一次，观察无残留/错乱）。(AC10/F10/N7)
- [x] 界面布局：启动含大写 NOVACODE 艺术字 banner + 名称版本 + cwd + 就绪提示行 + 底部「上横线 + `❯` 输入行 + 下横线 + 状态栏」输入框（尾随光标处，Enter 提交、Ctrl+J 换行）。（验证：启动截图比对）。(AC7/F7)

## 集成
- [x] TUI 通过统一 `Provider` Protocol 驱动两种协议，切换协议不改变上层交互（验证：分别用 anthropic / openai 配置跑同一组对话，行为一致）。(AC3/N3)
  > openai 协议（DeepSeek）已真实跑通；anthropic 协议经适配器代码 + 假 key 错误路径验证，待真实密钥补端到端。
- [x] 多轮上下文携带：先告知信息、后追问，模型能正确引用前文；退出再启动后历史为空（验证：两轮对话 + 重启验证）。(AC6/F6)
- [x] 流式不阻塞：等待/流式期间界面仍响应、不冻结（验证：长回复期间界面持续刷新；asyncio event loop 不阻塞）。(AC13/N1)
- [x] scrollback 渲染（Claude Code 风格）：对话输出直接打印进**终端 scrollback**（与命令行融为一体），用终端滚轮/滚动条回看完整历史，退出后历史仍在终端中；banner 全历史只出现一次（验证：tmux 多轮后回滚查看历史、统计 banner 次数 + 退出后历史仍在）。(AC7/F7)
- [x] base_url 覆盖：为某 provider 配自定义 `base_url`（兼容端点）可正常收发（验证：配一个兼容端点跑通一轮）。(F3)
- [x] 窗口自适应：缩放终端宽度后输入框边框/markdown 随宽度自适应、不错版，反复放大/缩小不残留旧边框
  （验证：运行中反复调整终端宽度、放大缩小多次，无残留横线、历史仍可见）。输入框顶部以透明 spacer 占满
  剩余高度，使 prompt_toolkit 每次重绘覆盖整屏，从而清掉 reflow 残留。(N6)

## 编译与测试
- [x] `python -m novacode` 能正常启动（在合法配置下进入 TUI）。以 inline 模式启动：不清屏、不切换 alternate screen，在当前窗口下方进入对话（Claude Code 风格），退出后 shell 恢复、无 `启动失败` 报错。
- [x] `ruff check .` 无告警。
- [x] `ruff format --check .` 通过（或本地 `ruff format .` 已统一格式）。
- [x] `pytest` 通过（`tests/test_config.py`、`tests/test_conversation.py`、`tests/test_tui.py`，共 16 项）。
- [ ] （可选）`mypy src/novacode` 通过（启用 strict 子集亦可）。
- [x] 密钥不回显/不打印：对话区与任何输出均不出现 `api_key`（验证：通读运行输出、检索无明文 key）。(N5)
  > 错误消息由 DeepSeek/OpenAI SDK 打码返回（如 `****-000`），本程序不打印明文 api_key。

## 端到端场景
- [ ] 场景 1（anthropic 多轮）：单条 anthropic 配置启动 → 连续两轮、第二轮引用第一轮 → 流式 + 计时 + markdown 定型 → `/exit` 退出。
  > 同流程已用 openai 协议（DeepSeek）完整跑通；anthropic 待真实密钥补跑。
- [x] 场景 2（openai 流式）：openai 协议配置 → 发一条含代码块的请求 → 流式逐字后 markdown 渲染正确。
- [x] 场景 3（多 provider 选择）：两条配置 → 启动出现列表 → 选第二条 → 状态栏显示其 name/model → 正常对话。
- [x] 场景 4（错误恢复）：错误 key 触发失败 → 对话区红色错误、程序不退出 → 修正后（重启）继续正常对话。
```
