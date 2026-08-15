"""ch06 smoke：端到端验证权限引擎装配与缓存策略（非交互、`Mode.BYPASS`）。

- 非交互无法人在回路，故以 `Mode.BYPASS` 运行（黑名单/沙箱仍拦，Ask 被跳过，避免阻塞）；
- 引擎根于 `cwd`，确认 smoke 用例的文件操作目标均在 cwd 子树内（否则会被沙箱拦）；
- 连发两条消息，打印每轮 token 用量（input / output / cache_write / cache_read）——
  同一会话连续请求时，稳定前缀应被缓存复用（Anthropic 次轮 cache_read>0；OpenAI 兼容端点看 cached_tokens）。

用法：`uv run python examples/smoke.py`
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from novacode.agent import Agent
from novacode.config import load
from novacode.conversation import Conversation
from novacode.llm import new_provider
from novacode.permission import Mode, new_engine
from novacode.tool import new_default_registry


async def main() -> None:
    cfg = load(".novacode/config.yaml")
    provider = new_provider(cfg.providers[0])
    registry = new_default_registry()
    conv = Conversation()
    engine, _ = new_engine(str(Path.cwd().resolve()))
    agent = Agent(provider, registry, version="smoke", engine=engine)

    prompts = ("用一句话介绍你自己", "再说一遍你叫什么名字")
    for i, text in enumerate(prompts, 1):
        conv.add_user(text)
        print(f"--- 第 {i} 条消息 ---")
        async for ev in agent.run(conv, Mode.BYPASS, asyncio.Event()):
            if ev.text:
                print(ev.text, end="")
            if ev.usage is not None:
                u = ev.usage
                print(
                    f"\n[用量] input={u.input} output={u.output} "
                    f"cache_write={u.cache_write} cache_read={u.cache_read}"
                )
            if ev.done:
                break
        print()


if __name__ == "__main__":
    asyncio.run(main())
