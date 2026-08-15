"""AgentTool 的 worktree 隔离分支（ch14 T11，spec F21/F22）。

`_execute_with_worktree` 在 SubAgent 启动前创建临时 Worktree，把 ctx cwd 注入到子
Agent 的工具调用，跑完后按变更自动清理。worktree 包只依赖标准库 + git，不依赖 agent，
无导入循环。
"""

from __future__ import annotations

from pathlib import Path

from ..tool.ctx import with_cwd
from ..worktree import Manager, random_agent_name


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    """构造注入子 Agent 的 worktree 上下文提示（spec F22，中文友好）。"""
    return (
        "<worktree-context>\n"
        "你当前在一个独立的 Git Worktree 副本中工作，与父 Agent 隔离。\n"
        f"- 父目录: {parent_cwd}\n"
        f"- 你的工作目录: {wt_path}\n"
        "- 父 Agent 提到的绝对路径基于父目录，你需要翻译成本地路径（替换前缀）再读写\n"
        "- 编辑文件前，必须先在本地 Worktree 重新 `read_file` 一次，避免使用过时内容\n"
        "</worktree-context>"
    )


async def _execute_with_worktree(
    manager: Manager, definition, sub_agent, sub_conv, prompt: str, events
) -> str:
    """在独立 Worktree 里跑子 Agent 到完成，返回最终文本（spec F21）。

    - name 用随机临时名（`sweep_stale` 正则匹配，规避同类型并发冲突）；
    - `create(name, "HEAD", manual=False)` → notice 前缀 → `with_cwd(wt.path)` 包住
      `run_to_completion` → `auto_cleanup`；有变更时把保留信息追加到结果文本。
    """
    name = random_agent_name()
    wt = await manager.create(name, "HEAD", manual=False)
    cwd = str(Path.cwd())
    notice = build_worktree_notice(cwd, wt.path)
    task_text = notice + "\n\n" + prompt
    with with_cwd(wt.path):
        final_text = await sub_agent.run_to_completion(sub_conv, task_text, events)
    report = await manager.auto_cleanup(name)
    if report.kept:
        final_text += f"\n[Worktree 保留: {report.path}, 分支 {report.branch}]"
    return final_text
