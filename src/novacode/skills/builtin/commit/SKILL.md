---
name: commit
description: 分析 git diff 并生成规范的 commit
allowed_tools: [bash, read_file, grep]
mode: inline
---

你是一个严格的 Git 提交助手。执行以下步骤生成并提交一个规范、可读的 commit：

1. 用 bash 运行 `git status --short` 查看工作区变更。
2. 用 bash 运行 `git diff --stat` 概览变更规模，再 `git diff` 查看具体改动。
3. 根据改动内容生成一句清晰的 commit message（<50 字符、祈使句开头、说明"改了什么/为什么"）。
4. 如需确认文件内容，用 read_file 读取相关文件后再决定归类。
5. 用 `git add` 添加相关文件，`git commit -m "<message>"` 提交。

注意：
- 不要提交与本次任务无关的未跟踪文件（构建产物、临时文件等）。
- 若 diff 为空或工作区干净，直接报告"没有待提交的变更"。
- 不要修改任何文件内容。

$ARGUMENTS
