"""InstallSkill zip 解压与 zip-slip 防护单测（ch11 T18）。"""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from novacode.skills import install as install_mod
from novacode.skills.catalog import Catalog
from novacode.skills.install import _safe_extract, install_from_url


def _zip_bytes(entries: list[tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buf.getvalue()


def test_zip_slip_rejected(tmp_path, monkeypatch):
    """含 `..` 路径 → ValueError 含 unsafe path，且无任何文件落盘（N8/AC15）。"""
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: tmp_path)
    data = _zip_bytes([("myskill/SKILL.md", "body"), ("../../etc/passwd", "x")])
    with pytest.raises(ValueError, match="unsafe"):
        _safe_extract(data)
    # 两阶段校验：恶意条目在写入前拒绝，第一条合法条目也未落盘
    assert not (tmp_path / ".novacode" / "skills" / "myskill").exists()


def test_invalid_top_dir_rejected():
    """顶层目录名违规（大写）→ 拒绝（F31）。"""
    data = _zip_bytes([("BadName/SKILL.md", "x")])
    with pytest.raises(ValueError, match="invalid skill name"):
        _safe_extract(data)


def test_happy_extract(tmp_path, monkeypatch):
    """合法 zip 解压到 ~/.novacode/skills/<top>/（N8 无副作用以外）。"""
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: tmp_path)
    data = _zip_bytes([("myskill/SKILL.md", "---\nname: myskill\ndescription: d\n---\nbody")])
    top = _safe_extract(data)
    assert top == "myskill"
    assert (tmp_path / ".novacode" / "skills" / "myskill" / "SKILL.md").is_file()


def test_install_from_url_flow(tmp_path, monkeypatch):
    """完整流程：下载（mock）→ 解压 → catalog.reload → 新 Skill 立即可见（F32）。"""
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: tmp_path)
    data = _zip_bytes([("myskill/SKILL.md", "---\nname: myskill\ndescription: d\n---\nbody")])

    async def _fake_download(source: str) -> bytes:
        return data

    monkeypatch.setattr(install_mod, "_download", _fake_download)
    catalog = Catalog.load(tmp_path)
    top = asyncio.run(install_from_url("http://x/s.zip", catalog, tmp_path))
    assert top == "myskill"
    assert "myskill" in catalog.names()
    assert catalog.get("myskill").meta.description == "d"


def test_zip_symlink_rejected(tmp_path, monkeypatch):
    """symlink 条目 → 拒绝（N8）。"""
    monkeypatch.setattr("novacode.skills.catalog._home", lambda: tmp_path)
    # symlink 单独出现且前无合法条目时也拒绝，不写盘
    # 构造一个带 symlink external_attr 的 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("myskill/link")
        zi.external_attr = (0o120000 << 16) | 0o777  # symlink 类型位
        zf.writestr(zi, "target")
    with pytest.raises(ValueError, match="symlink"):
        _safe_extract(buf.getvalue())
