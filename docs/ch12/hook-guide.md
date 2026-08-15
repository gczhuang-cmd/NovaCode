# ch12 Hook 系统功能与配置指南

> Hook 系统的完整能力介绍 + 配置参考 + 代码导读。本文描述的是已实现行为（`docs/ch12/spec.md` 的需求在实现上的落地），与 `checklist.md` / `test-guide.md` 配套。

## 1. 概述

**Hook = 在 Agent 生命周期的固定时刻，用 YAML 配置自动执行一段动作，全程不写代码。**

设计上每条 hook 是「**事件 + 条件 + 动作**」三要素：
- **事件**：11 个固定触发时刻（SessionStart、PreToolUse、Stop…）
- **条件**（可选）：满足才触发（匹配事件 payload 的字段）
- **动作**：触发后执行什么（shell / prompt / http / subagent）

本质是「应用级横切」，近似 AOP 的 before/after/around 通知，但**纯配置化、面向使用者**（详见对话中关于 AOP 类比的说明）。

## 2. 能做什么（6 大类用途）

| 用途 | 场景 | 组合方式 |
|------|------|---------|
| 安全护栏 | 拦截危险命令、拦截写敏感路径、拦截「delete」类输入 | `PreToolUse`/`UserPromptSubmit` + `shell` exit 2 |
| 自动格式化/检查 | 写完 py 自动 `ruff format`、写完跑单测 | `PostToolUse` + `shell` + `async` |
| 外部通知 | 对话结束发 IM/企微、工具失败告警 | `Stop`/`PostToolUse`/`Notification` + `http` |
| 上下文注入 | 强制中文、注入团队规范、每轮提醒 | `SessionStart`/`PreUserMessage` + `prompt` |
| 会话生命周期 | 启动初始化、退出清理、`/clear`/`/resume` 切换 | `SessionStart`/`SessionEnd`/`SessionResume` |
| 审计/记录 | 记录每次工具调用、记录用户输入 | `PreToolUse`/`UserPromptSubmit` + `shell` 写日志 |

## 3. 配置语法（完整参考）

```yaml
hooks:
  - name: 唯一名（必填，仅用于日志 / only_once / 冲突检测）
    event: 事件名（必填，11 选 1）
    if:                    # 可选，省略=无条件触发
      all_of:              # 或 any_of，二选一；两者同时出现=加载错误跳过
        - field: payload字段路径（如 tool_input.path）
          match:
            type: exact | glob | regex | not
            value: "..."   # not 类型时改用 inner: {type, value}
    action:                # 必填
      type: shell | prompt | http | subagent
      # 各类型独有字段见第 5 节
    only_once: true        # 可选，默认 false：会话内只跑一次
    async: true            # 可选，默认 false：后台执行不阻塞；拦截事件(PreToolUse/UserPromptSubmit)禁用
    timeout: 30s           # 可选，默认 30s：命令/HTTP 最大执行时长（支持 30s / 5m / 1h / 秒数）
```

配置文件扫描（顺序）：项目级 `<项目根>/.novacode/hooks.yaml` → 用户级 `~/.novacode/hooks.yaml`，**两层叠加合并**，同名 hook 后到者跳过（stderr 提示）。加载错误一律 stderr 提示后跳过该条，**不阻断启动**。

## 4. 11 个事件参考

**每个事件 payload 都含通用字段**：`event`、`session_id`、`cwd`、`mode`（default/plan/… 展示名）。

| 事件 | 触发时机 | 特有字段 | 可拦截 |
|------|---------|---------|:---:|
| `SessionStart` | 启动首次进会话、`/clear` 新建会话后 | — | |
| `SessionEnd` | 进程退出（cli 兜底统一 emit） | — | |
| `SessionResume` | `/resume` 恢复完成 | — | |
| `UserPromptSubmit` | 用户提交非命令消息、写历史前 | `prompt` | ✅ |
| `Stop` | Agent 自然停止、Done 事件前 | `iter` | |
| `PreUserMessage` | 每轮请求 LLM 前 | `prompt`（末尾 user 消息） | |
| `PreToolUse` | 工具执行前、权限检查前 | `tool_name`、`tool_input` | ✅ |
| `PostToolUse` | 工具结果拿到后（含权限 Deny / hook 拦截项） | `tool_name`、`tool_input`、`tool_result`、`is_error` | |
| `PreCompact` | 上下文压缩前（自动/紧急/手动） | `trigger`（auto/emergency/manual） | |
| `PostCompact` | 压缩返回后 | `trigger`、`before_tokens`、`after_tokens` | |
| `Notification` | 权限审批弹出、流式出错 | `kind`（approval/stream_error）、`detail` | |

## 5. 四种动作

### 5.1 shell —— 执行命令
```yaml
action:
  type: shell
  command: "echo blocked 1>&2 & exit 2"   # Windows cmd 语法；WSL 用 echo blocked >&2; exit 2
```
- 事件 payload 序列化为**单行 JSON 经 stdin** 传给命令，脚本侧可用 `jq` 取字段：
  `path=$(jq -r .tool_input.path)`（WSL 需 jq；Windows 可用 `python -c` 替代）
- **拦截事件下**（blocking）：
  - `returncode == 2` → 拦截命中，stderr（或 stdout）作拒绝原因
  - `returncode == 0` → 放行
  - 其它非 0 → hook 失败（记 stderr 日志），**不拦截**，Agent 继续
- 平台：Linux/WSL 由 `sh` 解释；Windows 走 cmd.exe（`&` 分隔、`1>&2` 重定向、输出按 GBK 解码）

### 5.2 prompt —— 注入 LLM 上下文
```yaml
action:
  type: prompt
  text: "请用简体中文回复"
```
- 文本拼进**下一轮** LLM 请求的 reminder 区（置于 plan reminder 之后），不写对话历史、不影响压缩
- 永不表达拦截

### 5.3 http —— 发外部请求
```yaml
action:
  type: http
  url: "http://127.0.0.1:9999/done"
  method: POST                       # 默认 POST
  headers: { "X-Token": "abc" }      # 可选
  body: "{event} happened"           # 可选模板；{字段} 取 payload；缺省为 payload JSON（key 字典序）
```
- 拦截事件下：响应 2xx 且 body `{"decision":"block","reason":"..."}` → 拦截；5xx / 网络错 / JSON 解析失败 → hook 失败**不拦截**

### 5.4 subagent —— 占位（本期不实现）
```yaml
action:
  type: subagent
  agent_name: foo
  prompt: "调研这个仓库"
```
- 加载期校验字段完整；执行期**仅打一行 stderr**：`[hook subagent] not yet implemented, skipped: foo`，不执行、不拦截

## 6. 条件表达式

```yaml
if:
  all_of:                                  # 全部命中才触发
    - field: tool_name
      match: { type: exact, value: write_file }
    - field: tool_input.path
      match: { type: glob, value: "**/*.py" }
    - field: is_error
      match: { type: exact, value: "False" }
```
- `all_of` / `any_of` 二选一（any_of 任一命中即触发）；缺省 `if` = 无条件
- `field` 用 `.` 取嵌套（`tool_input.command`）；路径不存在按空串处理，不报错

| match.type | 语义 | 示例 |
|-----------|------|------|
| `exact` | 整串相等 | `value: write_file` |
| `glob` | 通配；含 `/` 的目标按路径分段（`*` 不跨段、`**` 跨段） | `value: "**/*.py"` |
| `regex` | 正则 search | `value: "(?i)delete"` |
| `not` | 反向包装 | `inner: {type: exact, value: x}` |

字段值类型转换：bool/int/float 转字符串（`is_error=False` → `"False"`），嵌套对象转字典序 JSON。

## 7. 执行控制

- **`only_once: true`**：同一会话内首次匹配并执行后记入引擎集合（key=name），后续相同事件直接跳过；`/clear`、`/resume` 进新会话时清空（重置重新触发）；进程退出不持久化。
- **`async: true`**：新 asyncio task 后台执行，立即返回不阻塞主流程；**拦截事件（PreToolUse/UserPromptSubmit）不允许 async**（加载期报错跳过，异步无法表达拦截）。
- **`timeout`**：命令 / HTTP 最大执行时长，默认 30s；超时按 hook 失败处理。拦截事件下同步 hook 串行执行、以各条 timeout 累加。

## 8. 拦截机制详解

- **PreToolUse 拦截**：动作表达拦截 → reason 拼成 `[hook <name>] <reason>` 当 tool_result 回灌给 LLM，跳过权限引擎与真实工具执行；PhaseStart/PhaseEnd 仍照常 emit（is_error=True）。LLM 会看到拦截原因并调整行为。
- **UserPromptSubmit 拦截**：阻止该消息写入对话历史；scrollback 显示 `[hook <name>] <reason>`；非全屏架构下输入框重画（已提交文本不保留），用户重新输入。

## 9. 示例合集

**① 拦截危险 bash 命令**
```yaml
hooks:
  - name: block-danger
    event: PreToolUse
    if:
      all_of:
        - field: tool_name
          match: { type: exact, value: bash }
        - field: tool_input.command
          match: { type: regex, value: "rm -rf|mkfs|shutdown" }
    action:
      type: shell
      command: "echo 危险命令已拦截 1>&2 & exit 2"
```

**② 写 py 后后台自动 ruff（WSL 版本，依赖 jq + ruff）**
```yaml
hooks:
  - name: ruff
    event: PostToolUse
    if:
      all_of:
        - field: tool_name
          match: { type: exact, value: write_file }
        - field: tool_input.path
          match: { type: glob, value: "**/*.py" }
        - field: is_error
          match: { type: exact, value: "False" }
    action:
      type: shell
      command: "path=$(jq -r .tool_input.path); ruff format \"$path\""
    async: true
    timeout: 5s
```
（Windows 无 jq/ruff 时，可把 command 换成写日志演示 async 不阻塞，见 `test-guide.md` 场景 8）

**③ 对话结束发通知**
```yaml
hooks:
  - name: notify-stop
    event: Stop
    action:
      type: http
      url: "http://your-server/notify"
      body: '对话结束：{event} {session_id}'
```

**④ 强制中文 + 拦截 delete（组合）**
```yaml
hooks:
  - name: zh-cn
    event: PreUserMessage
    action: { type: prompt, text: "请始终用简体中文回复" }
  - name: warn-delete
    event: UserPromptSubmit
    if:
      all_of:
        - field: prompt
          match: { type: regex, value: "(?i)delete" }
    action:
      type: shell
      command: "echo 输入含 delete 已拦截 1>&2 & exit 2"
```

**⑤ 记录每次工具调用（审计，Python 提取 path）**
```yaml
hooks:
  - name: audit-tools
    event: PreToolUse
    action:
      type: shell
      command: "python -c \"import sys,json; d=json.load(sys.stdin); open(r'.novacode/tool-audit.log','a').write(d['tool_name']+' '+json.dumps(d['tool_input'])+chr(10))\""
```

## 10. 能力边界（本期刻意不做）

- subagent 动作真实执行（仅占位日志，待后续章节对接）
- hook 文件热更新（改配置需重启）
- 执行优先级 / order 字段（按 yaml 声明顺序自然有序）
- only_once 跨进程持久化（重启后重新触发一次）
- hook 间依赖 / 互斥、失败重试、独立日志文件、@include/继承

## 11. 代码导读（按学习顺序）

| 顺序 | 文件 | 看什么 |
|------|------|--------|
| 1 | `src/novacode/hook/event.py` | 11 个事件枚举、`is_blocking` |
| 2 | `src/novacode/hook/rule.py` | 数据结构：Rule / Condition / Action / Payload |
| 3 | `src/novacode/hook/loader.py` | YAML 配置 → Rule 对象：字段校验、两层合并、错误跳过 |
| 4 | `src/novacode/hook/matcher.py` | 条件求值：`eval_condition` / `get_by_path` |
| 5 | `src/novacode/hook/engine.py` | 分派主流程：`dispatch`、only_once 集合 |
| 6 | `src/novacode/hook/executor.py` | 四类动作执行：shell exit2 / http / prompt / subagent stub |
| 7 | `src/novacode/permission/matcher.py` | 匹配器底层：Exact/Glob/Regex/Not + `compile_matcher` |
| 8 | `src/novacode/agent/__init__.py` | 挂接点：搜 `_dispatch_hook`（PreToolUse/PostToolUse/Stop/PreCompact/…） |
| 9 | `src/novacode/tui/run.py` | 挂接点：`_dispatch_session_start` / `_dispatch_user_prompt_submit` |
| 10 | `src/novacode/command/builtin_ui.py` | `/clear`、`/resume` 的 SessionEnd/Start/Resume 挂接 |
| 11 | `src/novacode/command/builtin_hooks.py` + `cli.py` | `/hooks` 命令、启动装配 |

> 手动测试步骤见 `docs/ch12/test-guide.md`；验收清单见 `docs/ch12/checklist.md`。
