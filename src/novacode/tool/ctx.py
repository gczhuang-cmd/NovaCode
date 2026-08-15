"""explicit cwd 上下文传递：ContextVar + with_cwd / cwd_from_ctx / resolve_path（ch14 T8）。

项目首次引入 ContextVar 机制（spec F16）：主 Agent 或 SubAgent 在 ctx 内用
`with_cwd` 声明本次工具调用的基准目录，6 个核心工具经 `resolve_path` 解析相对路径。
asyncio task 自动继承 context，Tool schema 不变、prompt cache 不抖动。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path

_ctx_cwd: contextvars.ContextVar[str | None] = contextvars.ContextVar("cwd", default=None)


@contextmanager
def with_cwd(directory: str):
    """在 with 块内把 ctx cwd 设为 directory；空串不改变（spec F16）。"""
    if not directory:
        yield
        return
    token = _ctx_cwd.set(directory)
    try:
        yield
    finally:
        _ctx_cwd.reset(token)


def cwd_from_ctx() -> str | None:
    """取当前 ctx cwd；未设置返回 None（spec F16）。"""
    return _ctx_cwd.get()


def resolve_path(p: str) -> str:
    """把相对路径解析为绝对路径：优先 ctx cwd，其次进程 cwd（spec F16）。"""
    base = _ctx_cwd.get() or str(Path.cwd())
    if not p:
        return base
    pp = Path(p)
    if pp.is_absolute():
        return str(pp)
    return str(Path(base) / pp)
