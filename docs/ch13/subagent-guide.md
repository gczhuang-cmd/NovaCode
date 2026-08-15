# ch13 SubAgent 使用指南

> 面向**使用**的完整手册：怎么用内置角色、怎么调 Agent 工具、怎么管后台任务、怎么自定义角色。对应实现见 `docs/ch13/spec.md` / `plan.md`，本文只讲操作与配置。

## 1. 是什么

**SubAgent = 主 Agent 把独立任务分派给子 Agent 执行**，子 Agent 有独立对话上下文，跑完把结果交回主 Agent。

- **主 Agent**：你正在对话的这个（当前会话上下文）
- **子 Agent**：临时启动的工作进程，独立上下文、独立 token 计数，共享你的 LLM / 权限引擎 / Hook / 文件系统

你**不需要直接调工具**——用自然语言描述任务，主 Agent 会自己决定调 `Agent` 工具。

## 2. 内置 3 个角色

| 角色名 | 用途 | 工具集 | 权限 | maxTurns |
|--------|------|--------|------|:---:|
| `general-purpose` | 通用子 Agent，完整能力但独立上下文 | 全部工具 | default | 30 |
| `Explore` | 只读探索：搜索、阅读、理清调用链 | 全部工具去掉 `write_file`/`edit_file` | default | 30 |
| `Plan` | 只读规划：分析需求、制定计划，不执行 | 只读工具（plan 模式） | plan | 15 |

**怎么选**：
- 想并行干活、独立上下文 → `general-purpose`
- 只想查代码、不改文件 → `Explore`
- 先出方案再动手 → `Plan`

## 3. 快速上手（对话里直接说）

```text
用 Explore 子 Agent 找出 src/novacode/permission 下所有以 test_ 开头的函数，只统计数量，不要修改任何文件
```

主 Agent 会：
1. 调 `Agent` 工具（`subagent_type="Explore"`）
2. **首次可能触发审批**（default 模式下调 Agent 工具需确认，选「允许本次」）
3. 子 Agent 跑完 → 结果作为 tool_result 回灌 → 主 Agent 汇报

```text
Fork 出去一个子 Agent，统计这个项目所有 Python 文件总行数（不指定 subagent_type）
```

不指定角色 = **Fork 路径**：克隆当前对话 + 无条件后台，立即返回 `{task_id, status:"async_launched"}`，跑完结果以 `<task-notification>` 注入主对话。

## 4. Agent 工具参数

用户一般不直接写参数（主 Agent 会填），但了解含义有助于描述任务：

| 参数 | 必填 | 含义 |
|------|:---:|------|
| `prompt` | ✅ | 交给子 Agent 的任务指令 |
| `description` | ✅ | 一句话任务描述，供展示 |
| `subagent_type` | | 内置/自定义角色名；**留空 = Fork 路径** |
| `run_in_background` | | `true` 强制后台（Fork 忽略此字段，恒后台） |
| `name` | | 给子 Agent 命名，供 `SendMessage` 续派用 |
| `model` | | 预留（本期恒用父模型，不切换） |

## 5. 前台 vs 后台

| 模式 | 触发 | 行为 |
|------|------|------|
| **前台**（默认） | 不设 `run_in_background` | 主 Agent 等子 Agent 跑完拿到结果（阻塞当前回合） |
| **后台** | `run_in_background: true` | 立即返回 `async_launched`，主 Agent 继续做别的 |
| **后台** | Fork 路径 | 无条件后台 |
| **超时切后台** | 前台子 Agent 跑超 `120s` | 自动转后台，返回 `timed_out_to_background` |

## 6. 后台任务管理（4 个工具）

后台任务跑完，结果以 `<task-notification>` 自动注入主 Agent 下一轮。

| 工具 | 作用 | 示例对话 |
|------|------|---------|
| `TaskList` | 列出所有后台任务（id/name/status/tool_count） | 「调 TaskList 看现在有什么后台任务」 |
| `TaskGet` | 查看某个任务完整状态（含 result/err/usage） | 「调 TaskGet 看 task_xxx 详情」 |
| `TaskStop` | 取消一个后台任务 | 「调 TaskStop 停掉 task_xxx」 |
| `SendMessage` | 给已跑完的后台 Agent 续派新任务（同 id 复用） | 「调 SendMessage 给 worker1 发：再统计 cli.py」 |

> 主 Agent 也会**主动**用这些工具（比如你说「等它跑完」，它会自己 TaskGet 查询）。

## 7. 自定义 Agent 定义文件

### 放哪里

| 层级 | 路径 | 优先级 |
|------|------|:---:|
| 项目级 | `<项目根>/.novacode/agents/*.md` | 最高 |
| 用户级 | `~/.novacode/agents/*.md` | 中 |
| 内置 | 随包发布（不可改） | 低 |

同名定义高优先级覆盖（项目级 `explore.md` 覆盖内置 Explore）。

### frontmatter 字段

```markdown
---
name: wc-counter              # 必填：角色名，^[A-Za-z][A-Za-z0-9\-_]{0,31}$
description: 行数统计专家       # 必填：一句话描述
tools:                        # 可选：工具白名单（空 = 不收窄）
  - bash
disallowedTools:              # 可选：工具黑名单
  - write_file
  - edit_file
model: inherit                # 可选：haiku/sonnet/opus/inherit（本期不切模型）
maxTurns: 5                   # 可选：最大迭代轮数（缺省继承全局 25）
permissionMode: dontAsk       # 可选：default/acceptEdits/plan/bypassPermissions/dontAsk
background: false             # 可选：true 强制后台
---

你是一个专门统计代码行数的 Agent。
约束：
- 只能用 bash 跑 `wc -l <files>` 计行
- 答复必须以「[wc-counter]」开头，后跟一行汇总数字
```

`---` 之后的正文就是**子 Agent 的系统提示**（system_prompt）。

### 示例

```markdown
# 文件：.novacode/agents/wc-counter.md
---
name: wc-counter
description: 行数统计专家，只用 wc -l 计行
disallowedTools: [write_file, edit_file]
permissionMode: dontAsk
maxTurns: 5
---

你是一个行数统计 Agent，只能用 bash 跑 wc -l 计行，答复以「[wc-counter]」开头。
```

对话：`用 Agent 工具调 subagent_type=wc-counter，统计 README.md 和 src/novacode/cli.py 的行数`

## 8. 权限模型

- 子 Agent **共享父权限引擎**（沙箱/黑名单/规则集与主对话一致）
- `permissionMode` 控制兜底：
  - `dontAsk` → 规则未命中的 Ask 直接放行（最常用：子 Agent 无 UI 审批，靠它自动执行 bash 等）
  - `acceptEdits` → 放行文件写
  - `bypassPermissions` → 全 Allow（黑名单/沙箱仍拦）
  - 缺省 `default` → 规则未命中的 Ask 会被**默认拒绝**并回灌，子 Agent 自行调整（不弹窗）
- **子 Agent 内部不弹审批窗**（Ask 默认拒绝回灌）

## 9. 限制与注意

- **子 Agent 不能嵌套**：任何子 Agent 的工具列表里都没有 `Agent` 工具，无法再开子 Agent
- **`model` 字段本期不生效**：恒用主 Agent 的模型
- **后台任务不跨会话持久化**：退出 novacode 后任务全部丢失
- **改 agent 定义文件需重启 novacode**（启动时加载）
- 首次调 Agent 工具、或子 Agent 内 Ask 触发时，主 Agent 可能弹审批（default 模式）——选「允许本次」即可；想免审批可把 `Agent` 加入 `settings.local.yaml` 放行

## 10. 一个完整工作流示例

```text
你：用 run_in_background=True name=worker1 起一个 general-purpose 子 Agent，
    任务：统计 src/novacode 下所有 py 文件的总行数
主 Agent：● Agent(...) → {"task_id": "task_xxx", "status": "async_launched"}   # 立即返回，不阻塞

你：调 TaskList 看看后台任务
主 Agent：● TaskList({}) → [{"id": "task_xxx", "name": "worker1", "status": "running", ...}]

（后台跑完）主 Agent 下一轮收到：<task-notification> Task task_xxx (name="worker1"): completed ...
你：调 SendMessage 给 worker1 发：再列出 src/novacode/cli.py 的 import 块
主 Agent：● SendMessage(...) → {"task_id": "task_xxx", "status": "resumed"}     # 同 id 复用，继续跑
```
