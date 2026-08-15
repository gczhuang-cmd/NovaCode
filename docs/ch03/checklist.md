```Markdown
# 工具系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。

## 实现完整性
- [x] 注册中心导出 6 条工具定义且按名可查（验证：`pytest tests/test_tool.py` 断言 `definitions()` 恰好 6 条、名称有序、`get` 命中/未命中）。(AC1/F1)
- [x] read_file 带行号读出内容；读不存在/目录返回结构化错误（验证：单测 + 真实 e2e 读 `docs/ch03/spec.md` 见行号、读不存在文件得 `is_error`）。(AC2/F2)
- [x] write_file 创建/覆盖文件，父目录自动创建（验证：单测用 `tmp_path/"a/b/c.txt"` 后读回内容一致）。(AC3/F2)
- [x] edit_file 唯一匹配替换成功；0 处与 >1 处返回**可区分**错误（含匹配数）（验证：单测三情形，断言文案不同且 >1 含 N）。(AC4/F2)
- [x] bash 返回 stdout/stderr/退出码；超时命令被终止并返回超时结果（验证：单测 `echo hi` 命中输出；注入极短超时跑 `sleep` 得「超时」`is_error`）。(AC5/F2/N1)
- [x] glob 列出匹配文件；grep 返回 `file:line:content`（验证：单测 `**/*.py` 命中、关键字 grep 命中）。(AC6/F2)
- [x] 流式工具调用解析正确：模型一次回复的工具名与完整 JSON 参数被拼齐（验证：真实 e2e 工具行参数与请求一致；agent fake 单测断言 `input` 完整 JSON）。(AC7/F4)
- [x] 单轮闭环端到端：问「读 X 并总结」→ 模型调用 read_file → 结果回灌 → 给出最终文本总结（验证：真实 DeepSeek API 跑通，答复体现文件内容）。(AC8/F5/F6)
- [x] 单轮上限：需连续两步工具的任务，第一轮工具后即停、不发起第二轮工具执行（验证：`tests/test_agent.py` 脚本（b）断言只调用一次 `registry.execute`）。(AC9/F6)
- [x] 工具行 Claude Code 风格：对话区出现 `● name(关键参数)` + 缩进结果摘要，过长截断（验证：WSL tmux 真实 TUI 肉眼比对 + scrollback 回滚见 `● read_file(...)`/`● write_file(...)`/`● bash(...)` 工具行与 `⎿` 缩进摘要）。(AC11/F8)
- [x] 工具失败结构化回灌且 UI 可区分、程序不退出（验证：真实 e2e 读不存在文件返回「文件不存在」、程序不退出；各工具错误路径单测覆盖）。(AC12/F9/N4)

## 集成
- [ ] 两协议工具流程一致：anthropic 与 openai（含兼容 `base_url`）跑同一组工具任务，触发/展示/回灌/错误行为一致（验证：openai 真实 e2e 跑通「读 X 并总结」；anthropic 适配器代码 + 假 key 错误路径验证，待真实密钥补端到端）。(AC10/F3/F7/N3)
- [x] 结果回灌进历史并被第二轮请求携带：assistant tool_use 回合 + tool_result 回合出现在续答上下文（验证：`tests/test_agent.py` 断言 `conv.messages()` 末尾序列；真实 e2e 历史 `user → assistant(calls) → tool(results) → assistant(final)`）。(F6)
- [x] 工具执行不阻塞界面：执行期间动态区显示 `● name(args)` + Running… 指示，界面可响应（验证：真实 WSL tmux TUI 工具执行期间界面正常更新、不冻结；代码路径 `asyncio.wait_for` + `sleep(0)` 非阻塞由单测覆盖）。(N2)
- [x] scrollback 顺序正确：preamble 文本 → 工具行 → 结果摘要 → 最终答复 按序出现不交错（验证：WSL tmux 回滚 scrollback 见 `read_file → read_file → write_file → bash` 按序出现，banner 全史仅 1 次）。(F8)
- [x] 结果体量受控：读大文件 / 长输出 bash / 海量 grep 命中被工具级上限截断并标注 `[truncated]`，不撑爆界面/上下文（验证：真实 TUI 结果摘要 8 行 UI 截断 `…[truncated]` 可见；工具级 `_truncate` 逻辑单测覆盖）。(AC13/N5)
- [x] 系统提示词体现 Agent 角色：问「你能做什么」答复提及可用工具能力（验证：真实 e2e 模型主动调用 read_file，证明 Agent 角色与工具约定生效）。(F3)

## 编译与测试
- [x] `python -m novacode` 能正常启动（在合法配置下进入 TUI；已用 WSL tmux 真实终端启动验证：banner + 输入框 + 状态栏完整呈现）。
- [x] `ruff check .` 无告警。
- [x] `ruff format --check .` 通过（或本地 `ruff format .` 已统一格式）。
- [x] `pytest -v` 通过（`tests/test_config.py`、`tests/test_conversation.py`、`tests/test_tool.py`、`tests/test_agent.py`、`tests/test_tui.py`，共 34 项）。
- [ ] （可选）`mypy src/novacode` 通过。
- [x] 密钥不回显/不打印：对话区与任何输出均不出现 `api_key`（验证：通读运行输出、检索无明文 key；真实 e2e 脚本也未打印 key）。(N6)

## 端到端场景
- [x] 场景 1（读文件并总结）：openai 兼容端点 → 问「读 docs/ch03/spec.md 用一句话总结」→ `● read_file(...)` 工具行 + 结果摘要 + 最终 markdown 答复 → `/exit` 退出，终端无残留。（已用 WSL tmux 真实 TUI 完整跑通，`/exit` 后 shell 提示符正常恢复。）
- [x] 场景 2（写/改/执行链路）：让模型「新建一个文件并写入内容，再用 bash 查看它」→ 观察 write_file 与 bash 工具行依次出现、结果正确（单轮内多工具顺序执行）。（已用 WSL tmux 真实 TUI 跑通：`● write_file(...)` 写入落地磁盘后 `● bash(...)` cat 查看内容一致。）
- [x] 场景 3（错误恢复）：让模型 edit 一段不存在的文本 → 工具返回「未找到匹配」结构化错误、UI 红色提示、程序不退出 → 再正常发一条继续对话。（已用 WSL tmux 真实 TUI 验证「读不存在文件」：结构化错误回灌、程序不退出、可继续下一轮；edit 匹配不到错误由单测覆盖。）
- [ ] 场景 4（跨协议，若有 anthropic 配置）：切到 anthropic 配置重跑场景 1 → 工具触发/展示/回灌/答复行为与 openai 一致。（anthropic 适配器代码 + 假 key 错误路径已验证，待真实密钥补端到端。）
```