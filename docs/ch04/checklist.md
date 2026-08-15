```Markdown
# Agent Loop Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。

## 实现完整性
- [x] 多轮自动连环：需要连续两步工具的任务，Agent 无需中途催促即自动多轮执行工具直到给出最终答复（验证：WSL tmux 真实跑「读 spec.md → 据内容新建 summary.txt」，`read_file` 与 `write_file` 跨多轮自动出现、最终答复、文件真实落地）。(AC1/F1)
- [x] 自然完成停止：模型给出无工具调用的纯文本即停（验证：`tests/test_agent.py::test_plain_text_turn_stops_immediately` 断言一轮即停 + `done=True`）。(AC2/F2)
- [x] 迭代上限兜底：模型反复调工具时达到 `MAX_ITERATIONS` 即停并提示，不无限循环（验证：`tests/test_agent.py::test_max_iterations_stops` 断言恰好上限次请求后停 + `notice == NOTICE_MAX_ITER` + `last_role()=="assistant"`）。(AC3/F2)
- [x] 连续未知工具停止：连续 `MAX_UNKNOWN_RUN` 轮只产生未知工具调用即停；混入已注册工具则计数重置（验证：`tests/test_agent.py` 两用例）。(AC4/F2)
- [x] 流出错恢复：provider 流出错时停止本轮、发 `err`、程序不退出（验证：端到端改坏 `base_url` 发一条得 `Connection error`、程序不退出、可继续；`tests/test_agent.py::test_stream_error_recovers` 断言 err 后历史以 assistant 收尾）。(AC5/F2)
- [x] 事件流完备：Agent 对外事件含文本 / 工具开始 / 工具结束 / `usage` / `iter` / `notice` / `done` / `err`（验证：各单测断言事件字段；端到端界面实时显示文本增量、工具进度、轮次、最终答复，信息均来自事件流）。(AC6/F3)
- [x] 流式收集双路：文本实时显示的同时，完整工具调用（拼齐 JSON 参数）被收集用于下一轮（验证：单测断言 `ToolCall.input` 完整；端到端工具行参数与请求一致）。(AC7/F4)
- [x] 保序分批并发：一次回复含多个工具时，连续只读并发执行、有副作用串行，结果按原序回灌（验证：`tests/test_agent.py::test_concurrent_batch_preserves_order` 断言并发峰值 ≥2、`rw` 后开始、结果顺序==调用序；`test_batch_partial_completion_no_crash` 覆盖「批内部分先完成」回归）。(AC8/F5/N6)
- [x] 取消历史一致：执行中取消后历史配对合法（有 tool_results、末尾 assistant 文本、无悬空 tool_use）（验证：`tests/test_agent.py::test_cancel_keeps_history_valid` 断言 `conv` 序列 + 取消后同历史再跑纯文本不坏）。(AC9/F6)
- [x] 用户取消：流式态 Esc 或 Ctrl+C 中断本轮回空闲态、不退出；空闲态 Ctrl+C 退出（验证：WSL tmux 真实按 Esc 中断 bash 得「· 已取消」、回输入框不退出、再发一条正常对话）。(AC10/F7)
- [x] 用量展示：状态栏显示会话累计 token（输入/输出），随轮次增长更新（验证：曾误判「DeepSeek 不返回 usage」——实为 openai 适配器只在 `choices==[]` 的 chunk 检查 usage，而 DeepSeek 的 usage chunk 带 `choices=1` 被漏掉；修复后 WSL tmux 真实显示 `↑1.1k ↓136 tok`，多轮累加递增）。(AC11/F8)
- [x] 进度展示：流式态动态区显示当前迭代轮次（验证：WSL tmux 真实 Plan 调研见「思考中… (25s) · 第 4 轮」轮次递增）。(AC12/F9)
- [x] Plan Mode：`/plan` 后只出现只读工具与计划文本、无写/执行；`/do` 切回全工具并立即按计划执行（验证：WSL tmux 真实跑通——`/plan` 后仅 read_file 调研并出 5 步计划、`/do` 后 edit_file/write_file/bash 真实执行修改文件；`tests/test_agent.py::test_plan_mode_uses_read_only_tools` 断言 fake 收到仅只读定义）。(AC13/F10)

## 集成
- [ ] 跨协议一致：anthropic 与 openai（含兼容 `base_url`）跑同一多轮任务，触发/执行/回灌/用量/取消行为一致（验证：openai 真实多轮跑通；anthropic 适配器代码 + 假 key 错误路径验证，待真实密钥补端到端）。(AC14/F11/N3)
- [x] 多轮历史正确携带：每轮 `assistant(tool_use)` 回合 + `tool_result` 回合按序入历史并被下一轮请求携带（验证：`tests/test_agent.py` 断言 `conv` 序列；真实多轮里模型引用前轮工具结果）。(F6)
- [x] 界面不阻塞：多轮循环与工具执行（含并发批）期间 spinner / 轮次 / 计时持续刷新（验证：WSL tmux 真实多轮 + 慢 bash 期间界面正常更新、不冻结；Esc 取消即时响应）。(N2)
- [x] scrollback 顺序正确：跨多轮 preamble → 工具行 → 结果摘要 → 最终答复按序出现不交错，并发批的工具行按模型调用序排列（验证：WSL tmux 真实多轮回滚 scrollback 见各块按序、banner 全史仅 1 次；单 event loop 内 `live.console.print` 同步追加保序）。(N3)
- [x] 结果体量受控：大文件 / 长输出 / 海量命中被工具级上限截断标注 `[truncated]`，多轮累积不撑爆（验证：工具级 `_truncate` 逻辑单测覆盖；ch03 真实 UI 截断 `…[truncated]` 已见；多轮累积上限 `MAX_ITERATIONS` 兜底）。(N4)
- [ ] 取消无泄漏：取消后无挂起 asyncio task / 无未关闭 queue（验证：`tests/test_agent.py` 取消用例通过；端到端反复触发取消后继续对话正常；内存/句柄稳定未做压力实测）。(N5/N6)
- [x] 系统提示体现 Agent 循环：问「你能做什么」答复体现可多步使用工具完成任务（验证：SYSTEM_PROMPT 已增循环约定；真实多轮任务模型自动连续推进不等待）。(F3)

## 编译与测试
- [x] `python -m novacode` 能正常启动（在合法配置下进入 TUI；WSL tmux 真实终端启动验证）。
- [x] `ruff check .` 无告警。
- [x] `ruff format --check .` 通过（或本地 `ruff format .` 已统一格式）。
- [x] `pytest` 通过（`test_config`、`test_conversation`、`test_tool`、`test_agent`、`test_tui`，共 42 项）。
- [ ] （可选）`mypy src/novacode` 通过。
- [x] 密钥不回显：对话区与任何输出均不出现 `api_key`（验证：通读运行输出、检索无明文 key）。(N7)

## 端到端场景
- [x] 场景 1（多轮连环）：openai 兼容端点 → 「读 `docs/ch03/spec.md`，再据内容新建 `docs/ch03/summary.txt` 写一句话摘要」→ `read_file` → `write_file` 跨多轮自动出现 → 动态区轮次递增、状态栏用量增长 → 最终答复 → 文件真实落地。（已用 WSL tmux 真实跑通。）
- [x] 场景 2（用户取消）：发一个多步任务，中途按 Esc → 回空闲态不退出 → 再正常发一条继续对话（历史未坏，模型能引用被取消的任务）。（已用 WSL tmux 真实跑通。）
- [x] 场景 3（流出错恢复）：临时改坏 `base_url` 发一条 → 错误块 + 程序不退出 → 改回后继续正常对话。（已用 WSL tmux 真实跑通。）
- [x] 场景 4（Plan Mode）：`/plan` → 问一个改动类需求 → 只出现 read/glob/grep + 计划文本、无写/执行 → `/do` → 切回全工具并按计划执行（出现 write/edit/bash）。（已用 WSL tmux 真实跑通，模型产出 5 步计划并执行修改源码。）
- [ ] 场景 5（跨协议，若有 anthropic 配置）：切到 anthropic 配置重跑场景 1 → 多轮行为与 openai 一致。（anthropic 适配器代码 + 假 key 错误路径已验证，待真实密钥。）
- [x] 场景 6（迭代上限）：主要由 `tests/test_agent.py::test_max_iterations_stops` 确定性验证（断言恰好 `MAX_ITERATIONS` 次请求后停 + `NOTICE_MAX_ITER`）。
- [x] 场景 7（连续未知工具）：主要由 `tests/test_agent.py` 两用例确定性验证（`NOTICE_UNKNOWN_TOOLS` + 计数重置）。
```