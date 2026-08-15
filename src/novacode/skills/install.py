"""InstallSkill 核心：下载 zip、校验路径、解压到 ~/.novacode/skills/（ch11 T18）。

zip-slip 防护（N8/AC15）：拒绝 `..`、绝对路径、符号链接、顶层目录外的逃逸。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import httpx

from . import catalog

# 下载限制（N8 防 zip 炸弹 + 网络滥用）
MAX_ZIP_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60.0

# Skill 名规范（F3）
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


async def install_from_url(source: str, catalog, work_dir: Path) -> str:
    """下载 zip → 严格校验解压 → reload → 返回顶层目录名（F31/F32）。"""
    data = await _download(source)
    top = _safe_extract(data)
    catalog.reload(work_dir)
    return top


async def _download(source: str) -> bytes:
    """流式下载并限大小（>50MB 抛 ValueError）。"""
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        buf = bytearray()
        async with client.stream("GET", source) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_ZIP_BYTES:
                    raise ValueError("zip too large")
    return bytes(buf)


def _safe_extract(data: bytes) -> str:
    """解压 zip 到 ~/.novacode/skills/<top_dir>/。

    两阶段：先全量校验所有条目的路径（zip-slip 防抖），全部通过后才统一写入——
    保证恶意 zip 在任何文件落盘前就被拒绝（AC15 无副作用）。
    返回顶层目录名。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError("zip is empty")
        top = names[0].split("/")[0]
        if not _NAME_RE.fullmatch(top):
            raise ValueError(f"invalid skill name in zip: {top!r}")
        # 动态解析 _home（测试 monkeypatch catalog._home 时同步生效）
        target_root = (catalog._home() / ".novacode" / "skills" / top).resolve()
        planned: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in zf.infolist():
            name = info.filename
            if not name or name.endswith("/"):
                continue  # 目录条目跳过
            if not name.startswith(top + "/"):
                raise ValueError(f"unsafe path outside top dir in zip: {name}")
            parts = Path(name).parts
            if ".." in parts:
                raise ValueError(f"unsafe path in zip: {name}")
            if Path(name).is_absolute():
                raise ValueError(f"unsafe absolute path in zip: {name}")
            # symlink 判定（N8）
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"unsafe symlink in zip: {name}")
            dest = (target_root / Path(name).relative_to(top)).resolve()
            if not dest.is_relative_to(target_root):
                raise ValueError(f"unsafe path escape in zip: {name}")
            planned.append((info, dest))
        # 全部校验通过后统一写入
        for info, dest in planned:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info))
    return top
