"""Slug 校验单测（ch14 T1，spec F1/AC1）。"""

from __future__ import annotations

import pytest

from novacode.worktree.slug import flat_slug, validate_slug


@pytest.mark.parametrize(
    "name",
    ["alice", "team/alice", "v1.0", "a_b", "feature/x-1.y_z"],
)
def test_validate_slug_accepts(name: str) -> None:
    validate_slug(name)  # 不应抛异常


@pytest.mark.parametrize(
    "name",
    ["", "a" * 65, "..", "./x", "a//b", "/x", "a/", "a b", "a;b", "../etc"],
)
def test_validate_slug_rejects(name: str) -> None:
    with pytest.raises(ValueError):
        validate_slug(name)


def test_flat_slug() -> None:
    assert flat_slug("team/alice") == "team+alice"
    assert flat_slug("alice") == "alice"
