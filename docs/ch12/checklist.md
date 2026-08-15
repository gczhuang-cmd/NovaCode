# Hook 生命周期挂钩系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
>
> **验收状态：** 单测 517 passed（`pytest -q`）、ruff/mypy 全绿；端到端核心场景在 WSL tmux 实跑验证。未实跑的端到端场景已明确标注。

## 实现完整性

### 权限匹配器扩展

- [x] `permission.Matcher` Protocol 存在，四种实现（`ExactMatcher` / `GlobMatcher` / `RegexMatcher` / `NotMatcher`）各自可单独导入并运行（验证：`pytest tests/test_permission_matcher.py -v` 26 passed）
- [x] `permission.Rule` 已替换 pattern 为 `matcher` 字段，`parse_rule` 能识别 `=` / `~` / `!` 前缀，且 `Bash(~^npm (install|test)$)` 这类带括号正则可解析（验证：`tests/test_permission_rule.py::test_parse_rule_regex_nested_parens` 等通过）
- [x] `to_rule_set` 在 `parse_rule` 失败时输出 stderr 错误日志（验证：`tests/test_permission_settings.py::test_to_rule_set_skips_invalid` 用 capsys 捕获含 `parse failed`）

### Hook 包

- [x] `novacode.hook` 包存在且可导入（验证：`python -c "import novacode.hook"` 不抛 ImportError）
- [x] 11 个 `Event` 成员全部声明且 `is_blocking` 仅对 `PRE_TOOL_USE` / `USER_PROMPT_SUBMIT` 返回 True（验证：`tests/test_hook_event.py::test_event_count_and_values`、`test_is_blocking` 通过）
- [x] `load(...)` 能解析合法 YAML 并构造 Engine（验证：`tests/test_hook_loader.py::test_load_valid_two_hooks` 通过）
- [x] Loader 对字段缺失 / 枚举错 / async + 拦截事件冲突 / matcher 编译失败均报 stderr 并跳过该条（验证：`test_hook_loader.py` 各错误子用例通过，含 `test_load_async_blocking_conflict`、`test_load_invalid_regex_skips`）
- [x] `Engine.dispatch` 按声明顺序执行 rule 且拦截后中断后续（验证：`tests/test_hook_engine.py::test_dispatch_blocking_interrupts` 通过）
- [x] Executor 的 shell `returncode == 2` 触发 blocked、`returncode == 0` 放行、其它非 0 视为失败不拦截（验证：`tests/test_hook_executor.py::test_shell_exit2_blocked` / `test_shell_exit0_allowed` / `test_shell_exit1_failure_not_block` 通过）
- [x] Executor 的 HTTP 在 body 含 `{"decision":"block","reason":"..."}` 时触发 blocked（验证：`tests/test_hook_executor.py::test_http_block` 通过）
- [x] Executor 的 prompt 动作通过 `ExecutionResult.prompt` 字段返回文本（验证：`tests/test_hook_executor.py::test_prompt_action` 通过）
- [x] Executor 的 subagent 动作仅 stderr 输出占位日志、不阻塞（验证：`tests/test_hook_executor.py::test_subagent_stub_logs` 通过）
- [x] only_once 状态在 Hook 引擎上，`/clear` 与 `/resume` 时被 `reset_for_new_session` 清空（验证：`tests/test_hook_engine.py::test_reset_for_new_session`；`/clear` 清空由 `tests/test_agent_runtime.py::test_reset_for_new_session_clears_pending_reminders`；端到端见场景 6）

### agent / tui 集成

- [x] `Agent.__init__` 接受 `hook_engine` 参数，agent 内部 `_dispatch_hook` 在各 emit 点全部调用（验证：`tests/test_agent.py::test_hook_pre_tool_use_blocked`、`test_hook_pre_user_message_injects_prompt`、`test_hook_stop_emitted_before_done`、`test_hook_reminder_combines_with_plan_reminder` 通过）
- [x] `tui.run.run` 主循环在 UserPromptSubmit 拦截时不写历史、不发起请求、打印提示（验证：`tests/test_tui.py::test_user_prompt_submit_blocked` 通过；端到端见场景 4）
- [x] `tui.run.run` 装配完 Agent 后派发 SessionStart 事件（验证：`tests/test_tui.py::test_dispatch_session_start_injects_prompt` 通过；端到端见场景 2）
- [x] `/clear` / `/resume` / `/exit` 触发 SessionEnd（验证：`/clear` 端到端实跑验证 SessionEnd→SessionStart 切换（场景 6）；`/exit` 与 Ctrl+C 由 cli 退出兜底统一 emit（代码审查 `cli.py`）；`/resume` 切换离开旧会话前 emit SessionEnd（代码审查 `command/builtin_ui.py::handle_resume`））
- [x] `cli` 退出前兜底 SessionEnd（验证：cli 调用链审查 `cli.py::_amain`）
- [x] `/hooks` 命令注册到命令表（验证：`tests/test_command_builtins.py::test_register_builtins_all_registered` 含 hooks；`tests/test_tui_commands.py::test_hooks_command_lists` 输出格式正确；端到端见场景 8）
- [x] `pending_reminders` 在 `Agent.run` 每轮取出后被清空（验证：`tests/test_agent_runtime.py::test_pending_reminders_append_take` 通过）

## 集成

- [x] `hook.Engine` 与 `permission.Matcher` 共用同一套匹配实现（验证：`novacode.hook` 包不重复实现 exact/regex/glob，`hook/matcher.py` 与 `hook/rule.py` 均复用 `permission.matcher`）
- [x] `hook.Engine` 接入 `Agent.run` 后所有现有 agent 测试不破坏（验证：`pytest tests/test_agent.py tests/test_agent_runtime.py -v` 全过）
- [x] `hook.Engine` 接入 tui 后所有现有 tui 测试不破坏（验证：`pytest tests/test_tui.py tests/test_tui_commands.py tests/test_tui_completion.py tests/test_tui_thinking.py -v` 全过）
- [x] PreToolUse 拦截结果当 tool_result 回灌后，LLM 视角看到的是 `is_error=True` 的 `ToolResult`，`content` 含 `[hook <name>] <reason>`（验证：`tests/test_agent.py::test_hook_pre_tool_use_blocked` 断言 END 事件 result；端到端见场景 1）
- [x] reminder 注入路径与 plan reminder 协同——同一轮 LLM 请求的 reminder 串同时含两类（验证：`tests/test_agent.py::test_hook_reminder_combines_with_plan_reminder` 断言第二轮 reminder 同时含 plan 精简版与 hook 注入文本）

## 编译与测试

- [x] 项目可导入无错误：`python -c "import novacode"`
- [x] 入口可启动：`python -m novacode` 能进入对话（WSL tmux 多次启动验证）
- [x] 所有单元测试通过：`pytest` → **517 passed, 1 skipped**
- [x] ruff 检查通过：`ruff check src tests` → All checks passed
- [x] 类型检查：`mypy src/novacode/hook` → Success（已安装 `types-PyYAML` 补 stub）

## 端到端场景（tmux 实跑）

每个场景在 tmux 内启动一个 novacode 实例完成，验证人工/可视化行为。

### 场景 1：PreToolUse shell 拦截 write_file

**预置：** 在 `.novacode/hooks.yaml` 写一条 hook（对应 AC4）：
```yaml
hooks:
  - name: block-write
    event: PreToolUse
    if:
      all_of:
        - field: tool_name
          match: { type: exact, value: write_file }
    action:
      type: shell
      command: "echo blocked by hook >&2; exit 2"
```

**步骤：**
- [x] tmux 启动 `python -m novacode` —— WSL 实跑
- [x] 给 LLM 输入「创建一个文件 hello.txt 内容是 hi」 —— 真实 deepseek 触发 write_file
- [x] LLM 应触发 write_file，工具被拦截 —— scrollback 显示 `● write_file({...})` 被拦
- [x] scrollback 内 tool_result 显示 `[hook block-write] blocked by hook`、文件未创建 —— `ls hello.txt` 确认未创建
- [x] LLM 收到反馈后调整回应，不死循环 —— 截图见 LLM 输出「写入被 hook 拦截了。让我检查一下拦截原因」并改用 bash

### 场景 2：SessionStart prompt 注入

**预置：**（对应 AC6）
```yaml
hooks:
  - name: zh-cn-default
    event: SessionStart
    action:
      type: prompt
      text: "默认用 zh-CN 回复"
```

**步骤：**
- [x] tmux 重启 novacode —— 重启后输入 `hi`
- [x] 立刻发一句英文输入「hi there」 —— 实际输入 `hi`
- [x] LLM 应该用中文回复（因为 reminder 区注入了 zh-CN 指令） —— LLM 以「你好！我是 NovaCode…」中文回复

### 场景 3：PostToolUse async shell 后台 ruff format

**预置：**（对应 AC7）

**步骤：**
- [x] tmux 启动 novacode —— WSL 实跑
- [x] 让 LLM 写一个故意排版不整齐的 Python 文件 —— LLM 写 `bad_format.py`（2 空格缩进）
- [x] LLM 完成写入后主对话立即进入下一轮，不停顿 —— 写入后回复用时 4.5s、输入框立即重画（async 后台不阻塞）
- [x] 验证文件被 `ruff format` 格式化 —— hook 日志 `1 file reformatted`，`bad_format.py` 变为 4 空格缩进

> 备注：环境无 jq/ruff，已安装 `ruff`（WSL 系统 python3，`--break-system-packages`）；hook 命令用 python 提取 path 替代 jq。

### 场景 4：UserPromptSubmit 拦截 delete 关键字

**预置：**（对应 AC10）

**步骤：**
- [x] tmux 启动 novacode —— WSL 实跑
- [x] 输入「请帮我 delete 那个文件」 —— 真实输入
- [x] 输入被拦截，scrollback 内显示 `[hook warn-delete] prompt contains delete keyword` —— 实跑确认
- [x] **该消息未进入对话历史、LLM 端未收到这条 user 消息（不发起请求）** —— 拦截后 usage 未增长、无请求
- [x] **输入框已重画（非全屏架构每轮重建输入框，已提交文本不保留），用户需重新输入** —— 实跑确认 `❯` 空框重画

> 说明：原文档「输入框内容仍在（被退回用户重新编辑）」基于 Textual 常驻输入框设想；实际 prompt_toolkit 非全屏架构下输入框每轮重建，拦截即 `continue` 重画，文本不保留。行为验收以「消息未入历史、未发起请求、有提示」为准。

### 场景 5：Stop HTTP 通知

**预置：**（对应 AC13）本地起接受 POST 的 echo server

**步骤：**
- [x] tmux 启动 novacode —— WSL 实跑
- [x] 让 LLM 简单回答一个问题后停止 —— 输入 `hi`，自然完成
- [x] echo server 收到一次 POST，body 含 `"event":"Stop"` —— 收到 `{"cwd":..., "event":"Stop", "iter":1, "mode":"default", "session_id":...}`（key 字典序，N6）

### 场景 6：only_once + PreUserMessage

**预置：**（对应 AC9）hook 写 `/tmp/novahook_first.log` 记录触发次数

**步骤：**
- [x] tmux 启动 novacode —— WSL 实跑
- [x] 第一轮发任意消息，stderr 出现 `first-turn-fired` —— 触发计数 1
- [x] 第二轮发消息，stderr 没有再次出现 —— 触发计数仍 1（only_once 跳过）
- [x] 执行 `/clear` 进新会话，再发消息，stderr 重新出现 `first-turn-fired` —— 触发计数变 2（/clear 重置）

### 场景 7：错误配置不阻断启动

**预置：**（对应 AC8）含 async + PreToolUse 非法 hook

**步骤：**
- [x] tmux 启动 novacode —— WSL 实跑
- [x] novacode 启动期 stderr 打印 `hook "bad-async": async not allowed for blocking events, skipped` —— 启动截图确认
- [x] novacode 仍然成功进入 idle 状态 —— banner + 输入框正常显示
- [x] `/hooks` 命令仅列出 `good-hook`、未列 `bad-async` —— 实跑确认（端到端中 `/hooks` 列出 3 条合法 hook、bad-async 被排除）

### 场景 8：`/hooks` 命令

**预置：** 一份包含 3 条合法 hook 的 `hooks.yaml`

**步骤：**
- [x] tmux 启动 novacode —— WSL 实跑
- [x] 输入 `/hooks` 回车 —— 实跑确认
- [x] 输出按 event 分组，每条一行 `  <name>  <event>  <action.type>  [flags]` —— 输出 `SessionStart:` / `UserPromptSubmit:` / `PreToolUse:` 分组，`[once]`/`[async]` 标志正确（rich `\[` 转义）
- [x] 末尾显示 `Loaded from: .../hooks.yaml` —— `Loaded from: /mnt/d/NovaCode/.novacode/hooks.yaml`

### 场景 9：端到端组合（AC17）

**预置：** `hooks.yaml` 包含场景 1、2、3、4 全部 hook

**步骤：**
- [x] tmux 启动 novacode —— WSL 实跑（组合 hooks.yaml 含场景 1/2/3/4）
- [x] 首轮：SessionStart 注入 zh-CN（场景 2），Agent 准备就绪 —— 全程中文交互，注入生效
- [x] 输入「帮我创建 hello.py，然后 ruff format 一下」 —— 真实 deepseek 执行
- [x] LLM 调 write_file 创建文件 → 被场景 1 的 hook 拦截 → LLM 重试或换方式 —— `[hook block-write] blocked by hook` → LLM 改用 bash 创建 hello.py 并调 ruff format（`1 file left unchanged`）
- [x] 整个过程不卡顿、无未捕获异常栈 —— 回复用时 8.0s 正常完成、输入框重画
- [x] `/hooks` 命令仍可工作显示 4 条 hook —— 输出 4 条（含 `[async]` 标志）+ Loaded from

> 备注：组合中 `Bash(**)` 权限放行需用 `**` 而非 `*`——`Bash(*)` 对含 `/` 的命令串按路径分段不匹配（`match_pattern` 既有行为）。
