"""路径沙箱单测：项目内放行、项目外拒绝、祖先回退、软链接逃逸（N2）。"""

import os
import sys

import pytest

from novacode.permission.sandbox import eval_symlinks_or_ancestor, resolve_root, sandbox_ok


def test_sandbox_inside_existing(tmp_path):
    root = str(tmp_path.resolve())
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    assert sandbox_ok(root, "a.txt") is True
    assert sandbox_ok(root, str(f)) is True


def test_sandbox_inside_not_existing_ancestor_fallback(tmp_path):
    """项目内、但中间多级目录尚未创建的新建文件路径 → 祖先回退后放行。"""
    root = str(tmp_path.resolve())
    (tmp_path / "src").mkdir()
    assert sandbox_ok(root, "src/a/b/c.py") is True  # src 存在，a/b/c.py 不存在
    assert sandbox_ok(root, "brand_new/x.py") is True  # 整个父链都不存在


def test_sandbox_outside(tmp_path):
    root = str(tmp_path.resolve())
    assert sandbox_ok(root, "/etc/passwd") is False
    assert sandbox_ok(root, "../outside") is False
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    assert sandbox_ok(root, str(outside)) is False
    assert sandbox_ok(root, str(outside / "sub")) is False


def test_sandbox_empty_path_means_root(tmp_path):
    root = str(tmp_path.resolve())
    assert sandbox_ok(root, "") is True


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 软链接需特权，链式测试在 WSL/posix 跑")
def test_sandbox_symlink_escape(tmp_path):
    """项目内软链接指向项目外目录 → 先解析再比对，判 Deny。"""
    root = tmp_path.resolve()
    outside = tmp_path.parent / "symlink-outside"
    outside.mkdir(exist_ok=True)
    link = root / "link"
    link.symlink_to(outside)
    assert sandbox_ok(str(root), "link") is False
    assert sandbox_ok(str(root), str(link / "file.txt")) is False


def test_resolve_root_expands(tmp_path):
    r = resolve_root(str(tmp_path))
    assert os.path.isabs(r)


def test_eval_symlinks_or_ancestor_not_exists(tmp_path):
    """不存在目标解析到最近已存在祖先后拼回剩余段（完整目标路径）。"""
    resolved = eval_symlinks_or_ancestor(str(tmp_path / "a" / "b" / "c.py"))
    assert resolved == os.path.join(str(tmp_path.resolve()), "a", "b", "c.py")
