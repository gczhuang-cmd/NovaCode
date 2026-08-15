# UX01：流式期间实时反馈 Checklist

> 每项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [ ] 思考增量转发：provider 逐段吐出 `StreamEvent(thinking=…)`，不拼装、不混入正文（验证：`tests/test_llm.py` 思考用例通过）
- [ ] 工具增量转发：`tool_calls` 分片时逐段吐出 `tool_delta`，且流末仍产出完整 `tool_calls`（验证：`tests/test_llm.py` 增量用例通过）
- [ ] reasoning 用量解析：`usage.reasoning_tokens` 从 `completion_tokens_details` 透传到 agent/TUI（验证：`tests/test_llm.py`、`tests/test_tui.py` 断言）
- [ ] 旁路隔离：`Event.thinking/tool_delta` 透传 TUI，但 conversation 消息数与内容不含思考（验证：agent 转发测试断言 conv 无思考）
- [ ] `_ThinkingView`：滚动只留最近 3 行、展开显示全文、toggle/clear 生效（验证：`tests/test_tui_thinking.py` 全绿）
- [ ] `_ToolProgress`：多 index 聚合、字符数累计、`12.3K` 格式化（验证：`tests/test_tui_thinking.py` 全绿）

## 集成

- [ ] 阶段切换：`thinking → tool_delta → text` 事件依次驱动 Live 更新为「思考 / 工具进度 / 正文」（验证：`tests/test_tui.py` FakeLive update 断言）
- [ ] 让位规则：正文或工具增量到达时思考视图清空，无残留（验证：`tests/test_tui.py` 事件序列断言）
- [ ] Ctrl+O 消费：`expand` 信号触发 `thinking_view.toggle()`（验证：`tests/test_tui.py`）
- [ ] 完成统计：usage 带 `reasoning` 时输出「· 思考 N tokens」，不带时不输出思考段（验证：`tests/test_tui.py` console 断言）
- [ ] 状态栏累计 in/out 不受影响（验证：`tests/test_tui.py` `status_text` 现有用例不回归）

## 编译与测试

- [ ] `.venv/Scripts/python.exe -m pytest -q` 全绿（原 417 + 新增）
- [ ] `.venv/Scripts/python.exe -m ruff check src/novacode tests` 通过
- [ ] `.venv/Scripts/python.exe -m ruff format src/novacode tests` 无改动

## 端到端场景（WSL tmux + 真实 deepseek-v4-flash）

> 启动后 sleep 3-5s 再发命令；`capture-pane -p > 文件` 后 Windows 侧 Read 观察。

- [ ] 场景 1 思考滚动：输入长思考任务 → Live 区实时滚动思考内容、仅最近 3 行（验证：capture-pane 观察）
- [ ] 场景 2 Ctrl+O：思考中按 Ctrl+O → 显示完整思考并继续增长；再按恢复最近 3 行（验证：capture-pane 观察）
- [ ] 场景 3 正文接管：思考结束正文逐字出现、Live 区无思考残留（验证：capture-pane 观察）
- [ ] 场景 4 工具进度：模型写大文件 → 「正在生成 write_file 参数…(已 N 字符)」字符数增长；审批时机照常（验证：capture-pane 观察）
- [ ] 场景 5 完成统计：回复完成打印「· 用时 Xs · 思考 N tokens」（验证：capture-pane 观察）
- [ ] 场景 6 /resume：`/resume` 恢复刚结束的会话 → 对话正常继续、无思考残留/解析错误（验证：capture-pane 观察）
- [ ] 场景 7 取消：Esc / Ctrl+C 仍可取消当前回复（验证：capture-pane 观察）

## spec 对齐

| spec 验收标准 | checklist 条目 |
|---|---|
| AC1 思考滚动最近 N 行 | 实现完整性 #4、场景 1 |
| AC2 Ctrl+O 展开/折叠 | 集成 #3、场景 2 |
| AC3 正文接管无残留 | 集成 #2、场景 3 |
| AC4 工具参数字符进度 | 集成 #1、场景 4 |
| AC5 工具执行/审批不变 | 实现完整性 #2（完整 tool_calls 仍产出）、场景 4 |
| AC6 思考不进持久化 | 实现完整性 #4、场景 6 |
| AC7 取消不受影响 | 集成 #3、场景 7 |
| AC8 /resume 兼容 + 统计不重叠 | 集成 #5、场景 5/6 |
