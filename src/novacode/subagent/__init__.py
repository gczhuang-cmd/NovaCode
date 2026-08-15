"""SubAgent 角色定义系统（ch13）。

对外接口：`Definition` / `Source` / `Catalog` / `load_catalog` / `parse_definition` / `parse_file`。
"""

from __future__ import annotations

from .catalog import Catalog, load_catalog
from .definition import Definition, Source
from .parser import parse_definition, parse_file

__all__ = [
    "Catalog",
    "Definition",
    "Source",
    "load_catalog",
    "parse_definition",
    "parse_file",
]
