"""黑名单单测：已知高危命令命中、普通命令不命中；bypass 模式仍拦（N1）。"""

import pytest

from novacode.permission.blacklist import hits_blacklist

DANGEROUS = [
    "rm -rf /",
    "rm -fr /",
    "rm -rf /*",
    "rm -fr ~",
    "rm -rf $HOME",
    "rm -rf --no-preserve-root /",
    "rm -rf ~/*",
    ":(){ :|:& };:",
    "dd if=/dev/zero of=/dev/sda",
    "dd if=/dev/zero of=/dev/nvme0n1",
    "mkfs.ext4 /dev/sda1",
    "mkfs /dev/sdb",
    "echo x > /dev/sda",
    "chmod -R 777 /",
    "chmod -R 0777 /",
]

SAFE = [
    "rm -rf ./build",
    "rm -rf /build",
    "rm -f /tmp/x",
    "git status",
    "git push",
    "ls -la",
    "echo hi > /dev/null",
    "echo hello",
    "uv run pytest",
    "mkdir -p src/novacode",
]


@pytest.mark.parametrize("cmd", DANGEROUS)
def test_dangerous_hit(cmd: str) -> None:
    assert hits_blacklist(cmd), f"应命中黑名单: {cmd}"


@pytest.mark.parametrize("cmd", SAFE)
def test_safe_not_hit(cmd: str) -> None:
    assert not hits_blacklist(cmd), f"不应命中黑名单: {cmd}"
