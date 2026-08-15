```Markdown
# 系统提示工程化 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。

## 实现完整性
- [x] 模块化装配：系统提示由按优先级排列的固定模块拼成、模块间空行分隔（验证：`tests/test_prompt.py::test_assemble_order_and_separator` 断言 7 块、身份在工具使用之前）。(AC1/F1)
- [x] 挂载即扩展：新增一个模块只需出现在模块列表，装配自动按优先级插入，不改 `assemble_system` 逻辑（验证：`tests/test_prompt.py::test_optional_empty_modules_skipped` 传入额外/空模块断言按序落位）。(AC1/F1)
- [x] 可选空槽：自定义指令/已激活 Skill/长期记忆内容为空时装配跳过、不留多余空行（验证：`tests/test_prompt.py` 断言空模块不出现、无连续空行）。(AC2/F1)
- [x] 缓存确定性：连续两次构造稳定系统提示逐字节相等；改变环境信息不改变稳定块（验证：`tests/test_prompt.py::test_stable_determinism` 断言两次 `build_system_prompt()` 相等；稳定块不含 date/git/cwd 由模块内容保证）。(AC5/F3/N1)
- [x] 环境信息呈现：系统提示第二段含工作目录、平台、当前日期、git 状态、版本、模型；与稳定块分属不同内容块（验证：`tests/test_prompt.py` 断言 `Environment.render()` 含各项；`test_anthropic_system.py` 断言 system 为两个文本块；WSL tmux 真实问答目录/平台/日期正确）。(AC3/F2)
- [x] 双重强化：关键约定（优先用专用工具、编辑前必先读）在工具描述与系统提示模块文本中均出现（验证：`tests/test_prompt.py::test_double_reinforcement` 断言系统提示含相关语句；`edit_file`/`bash` 工具描述已补强化）。(AC7/F5)
- [x] 补充消息注入机制：`plan_reminder` 输出以 `<system-reminder>` 标签包裹（验证：`tests/test_prompt.py::test_system_reminder_wrapped` 断言标签存在）。(AC8/F6)
- [x] 缓存字段解析：provider 用量对外暴露缓存写/读；Anthropic 取 `cache_creation_input_tokens`/`cache_read_input_tokens`，OpenAI 取 `cached_tokens`，缺字段为 0 不抛异常（验证：`tests/test_agent.py::test_cache_usage_passthrough` 断言透传；`examples/smoke.py` 真实打印 DeepSeek 次轮 `cache_read=1024`）。(AC6/F4/N6)
- [x] Anthropic 缓存断点真实发出：稳定块序列化后带 `cache_control: {"type": "ephemeral"}`、环境块不带（验证：`tests/test_anthropic_system.py` 断言——守护回归）。(AC4/F3)

## 集成
- [x] 规划模式按轮次注入：`/plan` 后 iter1 注入完整提醒、间隔轮（每 4）重复完整、其余轮精简；reminder 不写入持久历史（验证：`tests/test_agent.py::test_plan_mode_read_only_tools_and_reminder` 断言 iter1 完整、iter2 精简、`conv.messages()` 不含 reminder 文本）。(AC9/F6/F7)
- [x] 规划模式工具集：规划模式 `req.tools` 仅只读、普通模式全量（验证：`tests/test_agent.py` 两测试断言两模式 tools 差异）。(AC9/F7)
- [x] 稳定系统提示跨模式一致：普通与规划模式 `req.system.stable` 相同（规划提醒已移出系统通道）（验证：`tests/test_agent.py::test_normal_mode_full_tools_and_stable_same_as_plan` 断言）。(F7/N1)
- [x] 历史合法：注入 reminder 后请求消息序列角色合法（Anthropic 并入末条 user、不产生连续 user）（验证：`test_anthropic_system.py` 断言 reminder 并入末条 user；端到端 plan 多轮调研无 400）。(AC12/N3)
- [ ] 跨协议一致：anthropic 与 openai（含兼容 base_url）都装配同一系统提示 + 环境段、注入同一 reminder（验证：openai 真实多轮跑通；anthropic 适配器代码 + mock 守护测试，待真实密钥看缓存命中）。(AC10/F8)
- [x] 不破坏 ch04：多轮连环、用户取消、流出错恢复、历史一致仍成立（验证：`pytest` 55 项全绿；WSL tmux 真实 Esc 取消后继续对话正常）。(AC11/N2)
- [x] 环境采集降级：非 git 目录/git 不可用时环境段对应项省略、不卡界面、请求正常发起（验证：`tests/test_prompt.py::test_environment_render_and_git_degrade` 非 git 临时目录 `git_status==""`）。(AC13/N4)
- [x] 界面不阻塞：环境采集（含 git 外调）不冻结界面、不显著拖慢首字（验证：真实对话首字延迟正常；git 调用以 `subprocess.run(..., timeout=2)` 收口，非 git 目录立即返回）。(N4)

## 编译与测试
- [x] `python -m novacode` 在合法配置下能正常启动。
- [x] `ruff check .` 无告警。
- [x] `ruff format --check .` 通过。
- [x] `pytest` 通过（`test_config`、`test_conversation`、`test_tool`、`test_agent`、`test_prompt`、`test_anthropic_system`、`test_tui`、`test_llm`，共 55 项）。
- [ ] （可选）`mypy src/novacode` 通过子集检查。(N2/N6)
- [x] 密钥不回显：对话区、环境段与任何输出均不出现 `api_key`（验证：通读输出、检索无明文 key；环境段只含 cwd/platform/date/git/version/model，不含环境变量）。(AC14/N5)

## 端到端场景（tmux 实跑）
- [x] 场景 1（缓存命中）：同一会话连发两条消息 → smoke/调试打印首轮 `cache_write>0`（Anthropic）、次轮 `cache_read > 0`，证明稳定前缀被缓存复用。（已用真实 DeepSeek 验证：第 2 条 `cache_read=1024`——openai 前缀缓存命中；Anthropic 断点机制有 mock 守护测试，真实 cache 命中待 anthropic 密钥。）
- [x] 场景 2（规划模式按轮次）：`/plan` 发一个需多步只读调研的任务 → 模型仅用只读工具产出计划、首轮注入完整提醒、后续轮精简；`/do` 切回全工具并按计划执行（ch04 已真实跑通完整流程，ch05 提醒改走消息通道后单测断言按轮次详略）。(AC9/F7)
- [x] 场景 3（reminder 不被当用户输入）：规划模式下模型不复述/回应 `<system-reminder>` 内容，而是据其约束行事（验证：WSL tmux 真实 plan 调研中模型未复述 reminder、直接只读调研）。(AC8/F6)
- [x] 场景 4（环境感知）：问「我现在在哪个目录、什么平台、今天几号」→ 模型据环境段正确回答（验证：WSL tmux 真实回答 /mnt/d/NovaCode / Linux / 2026-08-08）。(AC3/F2)
- [x] 场景 5（取消后可继续）：规划模式多轮中途按 Esc 取消 → 回空闲态、再发一条继续对话不报 400（验证：WSL tmux 真实 Esc 取消 bash 后继续对话、模型引用被取消上下文）。(AC12/N3)
- [x] 场景 6（非 git 目录降级）：在非 git 目录启动 → 环境段省略 git 状态、正常对话（验证：项目非 git 目录，`tests/test_prompt.py` 断言 `git_status==""`、环境段无 git 行）。(AC13/N4)
```