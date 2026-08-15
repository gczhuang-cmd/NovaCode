"""tool ctx 单测（ch14 T8，spec F16/AC13）。"""

from __future__ import annotations

from pathlib import Path

from novacode.tool.ctx import cwd_from_ctx, resolve_path, with_cwd


def test_resolve_absolute_path(tmp_path) -> None:
    abs_path = str(tmp_path / "a.txt")
    assert resolve_path(abs_path) == abs_path


def test_resolve_relative_with_ctx_cwd(tmp_path) -> None:
    with with_cwd(str(tmp_path)):
        assert resolve_path("a.txt") == str(tmp_path / "a.txt")
        assert resolve_path("sub/b.txt") == str(tmp_path / "sub" / "b.txt")


def test_resolve_relative_falls_back_to_process_cwd() -> None:
    assert resolve_path("a.txt") == str(Path.cwd() / "a.txt")


def test_resolve_empty_returns_ctx_cwd(tmp_path) -> None:
    with with_cwd(str(tmp_path)):
        assert resolve_path("") == str(tmp_path)


def test_with_cwd_empty_noop() -> None:
    with with_cwd(""):
        assert cwd_from_ctx() is None


def test_with_cwd_resets_after_block(tmp_path) -> None:
    with with_cwd(str(tmp_path)):
        assert cwd_from_ctx() == str(tmp_path)
    assert cwd_from_ctx() is None
