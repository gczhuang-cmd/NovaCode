"""沙箱 /tmp 白名单测试（ch15 N9/T33）。"""

from __future__ import annotations

from novacode.permission.sandbox import sandbox_ok


def test_tmp_whitelist(tmp_path):
    root = str(tmp_path)
    assert sandbox_ok(root, "/tmp/foo.txt")  # 开放 /tmp
    assert sandbox_ok(root, "/private/tmp/x/y")  # macOS 真实路径
    assert not sandbox_ok(root, "/etc/passwd")  # 系统文件仍拒
    assert sandbox_ok(root, "a.txt")  # 相对路径在 root 内


def test_root_based(tmp_path):
    root = str(tmp_path)
    assert sandbox_ok(root, str(tmp_path / "sub" / "f"))
    assert not sandbox_ok(root, str(tmp_path.parent / "other"))
    assert sandbox_ok(root, "")  # 空路径视为 root
