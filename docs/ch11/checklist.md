~~~Markdown
# Skill 技能包系统 Checklist

> 每一项通过运行代码或观察行为来验证。最后一节"端到端场景（tmux 实跑）"必须在 tmux 内实际跑过。
> ch11 修订：TUI 为 prompt_toolkit 非全屏 + CommandContext；启动命令为 WSL `/root/.venv-novacode/bin/python -m novacode`（Windows `.venv\Scripts\python.exe -m novacode`）。

## 实现完整性

- [x] `novacode.skills` 包可正常 import（验证：`python -c "import novacode.skills"` 无错）
- [x] `novacode.tool.load_skill` 与 `novacode.tool.install_skill` 可正常 import（验证：`python -c "from novacode.tool import load_skill, install_skill"`）
- [x] `novacode.prompt` 改造后单测通过（验证：`pytest tests/test_prompt.py`）
- [x] `novacode.command` 改造后单测通过（验证：`pytest tests/test_command*.py`）
- [x] `novacode.agent` 改造后单测通过（验证：`pytest tests/test_agent*.py`）
- [x] 整包可启动（验证：`python -m novacode` 不报错）
- [x] `ruff check .` 无新增告警
- [x] `ruff format --check .` 通过
- [x] 内置三个 Skill（commit / review / test）的 SKILL.md 通过 frontmatter 与 body 双重校验（验证：启动后 `/skill` 输出三行）

## Skill 定义与解析

- [x] 最简合法 SKILL.md（仅 name+description）被 parser 解析成功（`tests/test_skills_parser.py::test_parse_skill_dir_minimal` 绿）
- [x] 非法 name（大写/空格/超长）被拒绝（`test_parse_skill_dir_invalid_name` 绿）
- [x] `tool.json` 合法时解析为 ToolSpec 列表（`test_parse_skill_dir_with_tool_json` 绿）
- [x] 缺 SKILL.md 抛 `FileNotFoundError`（`test_parse_skill_dir_no_skill_md` 绿）

## Catalog 加载

- [x] 空 work_dir + 空 HOME 时 Catalog 仅含三个内置 Skill（`test_load_catalog_builtin_only` 绿）
- [x] 用户目录同名 Skill 覆盖内置（`test_load_catalog_user_override` 绿）
- [x] 项目目录同名 Skill 覆盖用户（`test_load_catalog_project_override` 绿）
- [x] 单个损坏 Skill 只跳过它本身，其它仍加载（临时 user 目录放损坏 + 合法两个，启动后只看到合法者）
- [x] Skill 名与 ch10 命令冲突时跳过加载（临时建 name=help 的 Skill，启动 stderr warning 且 `/help` 仍为内置）

## fail-fast 依赖检查

- [x] `allowed_tools` 引用不存在工具时，启动 stderr 输出对应错误并把该 Skill 从 Catalog 剔除（建 `allowed_tools: [NotExist]` 的 Skill，stderr 含 `allowed_tool "NotExist" not registered`，`/skill` 不出现它）
- [x] `load_skill` / `install_skill` 在 fail-fast 检查中被视为允许引用（建 `allowed_tools: [load_skill]` 的 Skill 正常加载）

## Slash Command 自动注册

- [x] 启动后 `/help` 含 `/commit [skill]`、`/review [skill]`、`/test [skill]` 三行且不再有独立 `/review`（tmux 键入 `/help`）
- [x] `/help` 含 `/skill` 一行（同上）
- [x] 用 Tab 补全输入 `/comm`，菜单展示 `/commit [skill]` 候选（tmux 实跑）

## 两阶段加载

- [x] System prompt 含 `## Available Skills` 区块，列出全部 Catalog Skill 的 `- name: description`（加 dump-prompt 测试用例或打断点观察 `build_system_prompt` 输出）
- [x] 未激活任何 Skill 时 env context 不含 `## Active Skills` 区块（`render_active_skills_block([]) == ""`）
- [x] 激活一个 Skill 后下一轮 env context 含 `## Active Skills` 区块包含该 Skill 的 body（单测覆盖 `render_active_skills_block`）

## LoadSkill 工具

- [x] 调用 LoadSkill({"name":"commit"}) 后 `active.names()` 包含 `"commit"`（单测）
- [x] LoadSkill 不存在的 name 返回 `unknown skill: <name>`，对话不中断（单测 + tmux）
- [x] LoadSkill 即便 `allowed_tools` 是空白名单也可见（`Registry.definitions_filtered([])` 含 `load_skill`）
- [x] LoadSkill 在 Plan Mode 下可调用，不被权限拦截（tmux：`/plan` 后让 LLM 触发 LoadSkill）

## /clear

- [x] `/clear` 之后 `runtime.active_skills.names()` 为空（tmux：先触发 LoadSkill 激活，再 `/clear`，观察 env context 无 Active Skills 块）
- [x] `/clear` 之后新会话可在 `/resume` 列表中看到旧会话条目（回归 ch10 N9 行为）

## Skill 执行器

- [x] inline Skill 执行后主对话历史新增一条 user 消息（tmux 触发 `/commit`，查看会话 JSONL）
- [x] inline Skill 的 SOP 顶部含 "This skill is designed to use only these tools: ..." 提示（`render_body` 单测）
- [x] fork Skill 跑完后主对话新增一条 assistant 消息（tmux 触发 `/review`，JSONL 末尾是 assistant）
- [x] fork Skill 失败（mock provider 出错）时返回的 assistant 消息为 `[skill <name> failed: ...]` 文本（执行器单测）

## tool.json 专属工具

- [x] 含 `tool.json` 的 Skill 被 LoadSkill 激活后，主工具注册中心新增对应工具名（tmux：放含 echo 的 `tool.json` 测试 Skill，激活后让 LLM 调用）
- [x] 专属工具 exec 超时 30 秒（`tests/test_tool_skill_tool.py`：`sleep 100` 脚本返回超时错误结果）
- [x] 专属工具 `returncode != 0` 视为失败，stderr 并入结果文本（单测）

## InstallSkill

- [x] 合法 zip 安装后 `~/.novacode/skills/<top_dir>/` 出现 SKILL.md（单测 + tmux）（单测已验证；tmux http server 端到端链路未实跑）
- [x] 合法 zip 安装后 `/skill` 立即列出新 Skill 且 `/<name>` 可调用（端到端）（单测已验证；tmux http server 端到端链路未实跑）
- [x] zip-slip（含 `..` 路径）被拒绝，`~/.novacode/skills/` 无副作用（`test_install_from_url_zip_slip`）
- [x] zip 内顶层目录命名违规时拒绝（单测）
- [x] InstallSkill 在 Plan Mode 下被权限引擎拦截（tmux：`/plan` → 自然语言让 Agent 装 Skill → 看到拦截）

## /skill 命令

- [x] `/skill` 首行输出 `Available skills (N):`，随后每条一行 `  /<name>  <description>`（按字典序、固定列宽对齐），末行 `Type /<skill-name> to invoke a skill.`（tmux 实跑）
- [x] Catalog 为空时 `/skill` 输出 `No skills loaded.`（清空内置 Skill 资源后启动）

## 编译与测试

- [x] `python -m novacode` 能正常启动（合法配置下进入 TUI）
- [x] `pytest` 通过（含新增 `tests/test_skills_*.py` 与 `tests/test_command_skills.py` 等）
- [x] `ruff check .` 无新增告警
- [x] `ruff format --check .` 通过
- [x] （可选）`mypy src/novacode` 通过

## 端到端场景（tmux 实跑）

> 在 tmux 内启动 novacode，按下面流程一步步操作；每步附"观察"项。

**前置**：
- `tmux new-session -d -s nova-ch11 -x 200 -y 50 "cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode 2>/tmp/novacode.err"`（Windows 本机用 `.venv\Scripts\python.exe -m novacode`）
- 准备 `~/.novacode/memory/MEMORY.md`（/memory 可列）

**步骤**：

1. **启动与就绪**
   - 操作：进程启动
   - 观察：banner（圆头实心 NOVACODE）正常；状态栏 `DEFAULT · <model>`；stderr 无 "skipped" 类错误（用户/项目目录干净时）

2. **`/help`**
   - 操作：键入 `/help` 回车
   - 观察：输出 15 行——ch10 内置命令（已无独立 `/review`）+ `/skill` + `/commit [skill]` + `/review [skill]` + `/test [skill]`

3. **`/skill`**
   - 操作：键入 `/skill` 回车
   - 观察：首行 `Available skills (3):`，随后三行 `  /commit ...` / `  /review ...` / `  /test ...`，末行 `Type /<skill-name> to invoke a skill.`

4. **显式调用 inline Skill `/commit`**
   - 操作：键入 `/commit` 回车
   - 观察：状态栏进入流式；AI 按 commit SOP 走（调 git status / diff）；本步骤真实操作，可 Esc 中断；验证 inline 路径联通

5. **显式调用 fork Skill `/review`**
   - 操作：主对话先随便说一句 "I just edited some files."，然后键入 `/review`
   - 观察：状态栏流式；AI 输出审查报告；主对话新增一条 assistant 消息；`fork_context=none` 意味着子 Agent 看不到 "I just edited..." 那条 user 消息

6. **意图触发 LoadSkill**
   - 操作：先在 `~/.novacode/skills/backend-interview/SKILL.md` 放一个 user-level Skill，再键入自然语言 "我想做后端面试准备"
   - 观察：LLM 调用 LoadSkill 工具，工具结果为 "Skill backend-interview activated..."；下一轮起 env context 出现该 Skill 的 SOP body

7. **`/clear` 清空激活**
   - 操作：键入 `/clear` 回车
   - 观察：新会话开启（notice 含新旧 session id）；接着聊一句，env context 不再含上一轮激活的 SOP（可让 Agent 复述"现在你激活了什么 Skill"间接验证）

8. **InstallSkill 安装第三方 Skill**
   - 操作：用 `python3 -m http.server 8080` 在本地托管 `test-skill.zip`（含 `myskill/SKILL.md`），在 novacode 输入 "把这个 skill 装下：http://localhost:8080/test-skill.zip"
   - 观察：Agent 调 install_skill 工具；安装成功后 `/skill` 立即出现 myskill；`/myskill` 可调用

9. **`/clear` → 新会话不残留**
   - 操作：激活 myskill 后 `/clear`，再 `/skill`
   - 观察：`/skill` 仍能看到 myskill（Catalog 与 Active 是两个概念，Catalog 不清）；env context 已无 Active Skills 块

10. **退出**
    - 操作：`/exit` 回车
    - 观察：进程优雅退出，无错误日志

## 验收报告模板

```
## 验收报告

### 通过
- [x] 实现完整性 — 全包启动：python -m novacode 输出 ...
- [x] /help 列表正确：含 /skill, /commit [skill] ...
- [x] /skill 输出三行内置 Skill ...

### 未通过
- [x] 第 X 项 — 预期：...，实际：...，修复方案：...

### 端到端
- [x] 启动与就绪 — 结果：banner 正常
- [x] /help — 结果：15 行命令
- [x] /skill — 结果：commit/review/test 三行
- ...（按上面 10 步逐条列出）
```
~~~
