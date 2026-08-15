# ch14 Worktree 手动测试指南

> 自己打开 novacode 实测 ch14 功能。每个场景给出可直接粘贴的输入与预期结果。
> 前提：`D:\NovaCode` 已是本地 git 仓库（ch14 开发时 `git init`），Worktree 功能已启用。

## 0. 准备（一次性）

**① 重建测试 Agent**（isolation:worktree 的子 Agent 定义，此前已删）：

`.novacode/agents/worktree-writer.md`：
```markdown
---
name: worktree-writer
description: 在 Worktree 内写文件的测试 Agent
permissionMode: dontAsk
maxTurns: 5
isolation: worktree
---

你是一个测试 Agent。当用户让你写文件时，直接用 write_file 工具写，不要询问。
```

**② 放行 Agent 工具（可选）**：主 Agent 调 Agent 工具在 default 模式会弹审批，重建放行配置可免去每次按 1：

`.novacode/settings.local.yaml`：
```yaml
permissions:
  allow:
    - Agent
```

启动：`uv run novacode`（或 `.venv/Scripts/python.exe -m novacode`）。

> 审批弹窗（default 模式调 Agent / bash 等命令执行类工具）按键：`1`=允许本次、`2`=永久允许、`3`=拒绝。

---

## 1. isolation:worktree 子 Agent 隔离写文件（最核心）

**输入：**
```
用 Agent 工具调 subagent_type=worktree-writer，prompt 是「把 scratch_ch14.txt 的内容覆盖为 SUBAGENT，只用 write_file 工具」
```

**预期：**
- 主 Agent 调 Agent 工具（可能弹审批，按 `1`）。
- 子 Agent 创建临时 Worktree，tool_result 末尾出现：
  `[Worktree 保留: ...\.novacode\worktrees\agent-aXXXXXXX, 分支 worktree-agent-aXXXXXXX]`
  （子 Agent 有写文件 = 有变更，auto_cleanup 保留）
- 主目录 `scratch_ch14.txt` 内容 **仍是原来的**（未被改）。
- Worktree 副本 `.novacode/worktrees/agent-a*/scratch_ch14.txt` 内容为 `SUBAGENT`。

**清理：** 删 `scratch_ch14.txt`；`/worktree remove agent-aXXXXXXX --discard` 删除保留的 Worktree。

## 2. 只读子 Agent 无变更自动清理

**输入：**
```
用 Agent 工具调 subagent_type=worktree-writer，prompt 是「用 read_file 读 CLAUDE.md 头 5 行，然后用 30 字总结」
```

**预期：**
- tool_result 是总结文本，**不含**「Worktree 保留」（只读无变更）。
- `.novacode/worktrees/` 下没有本次对应的 `agent-a*` 残留（auto_cleanup 已删）。

## 3. /worktree create + list 手动管理

**输入：**
```
/worktree create demo-feature
/worktree list
```

**预期：**
- create → `Worktree 已创建: ...\.novacode\worktrees\demo-feature (分支 worktree-demo-feature)`
- list → 一行含 `demo-feature`，带 `[manual]` 标记（手动创建）。

**外部验证（本机 PowerShell）：**
```
git -C .novacode/worktrees/demo-feature branch --show-current   # → worktree-demo-feature
```

**清理：** `/worktree remove demo-feature --discard`

## 4. /worktree exit 变更保护

**输入（依次）：**
```
/worktree create guard-test
/worktree enter guard-test
/worktree exit --remove
/worktree exit --remove --discard
```

**预期：**
- enter → `已进入 guard-test: ...`
- `exit --remove`（未加 --discard，此时无变更）→ **也删除成功**（无变更才走删除）
- 若要测变更保护：先在 `.novacode/worktrees/guard-test/` 里新建一个文件（如 `test.txt`），再 `exit --remove` →
  `Worktree 有未提交修改或本地新增 commit，拒绝删除；加 --discard 强制`
- 加 `--discard` 后 → `已退出并删除 Worktree`

## 5. /worktree enter 后工具调用走 Worktree 路径（explicit cwd）

**输入（依次）：**
```
/worktree create cwd-test
/worktree enter cwd-test
用 read_file 读 probe.txt
/worktree exit
用 read_file 读 probe.txt
```

**预置：** 在 `.novacode/worktrees/cwd-test/` 下建一个 `probe.txt`（内容随便，如 `in-worktree`），主目录不要有这个文件。

**预期：**
- enter 后 `read_file probe.txt`（相对路径）→ 命中 Worktree 里的文件内容。
- exit 后再次 `read_file probe.txt` → `文件不存在: probe.txt`（active_cwd 已回到主目录）。
  > 若第二次主 Agent 不调工具直接答，可加一句「必须实际调用 read_file 工具，不要凭记忆回答」。

**清理：** `/worktree remove cwd-test --discard`

## 6. Slug 校验阻止路径遍历

**输入（依次）：**
```
/worktree create ../etc
/worktree create ..
/worktree create normal_one
```

**预期：**
- `../etc` → `slug 段 '..' 不允许（路径遍历）`，不创建任何目录。
- `..` → 同样拒绝。
- `normal_one` → 成功创建。

**清理：** `/worktree remove normal_one --discard`

---

## 常见现象说明

| 现象 | 说明 |
|------|------|
| 启动提示 `Worktree 管理器降级` | 当前目录不是 git 仓库——Worktree 功能未启用，/worktree 命令报「Worktree 未启用」 |
| 子 Agent 报 `worktree manager not configured` | 同上，或 isolation:worktree 但启动时 Manager 构造失败 |
| 主 Agent 调 Agent 工具弹审批 | default 模式，Agent 工具属命令执行类；按 `1` 或重建 settings.local.yaml 放行 |
| Worktree 被「误保留」 | 变更检查把 `.novacode/` 与软链大目录（.venv 等）算进变更会保留——已修复忽略；若复现请报告 |
