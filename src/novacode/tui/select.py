"""provider 选择：打印列表 + 输入序号/名称。"""

from __future__ import annotations

from prompt_toolkit.shortcuts import print_formatted_text, prompt
from rich.text import Text

from ..config import ProviderConfig


def choose_provider(providers: list[ProviderConfig]) -> ProviderConfig:
    """打印各 provider 列表，让用户输入序号或名称选择。"""
    print_formatted_text(Text("请选择 provider：", style="bold"))
    for i, p in enumerate(providers, 1):
        print_formatted_text(f"  {i}. {p.name} ({p.model})")
    while True:
        choice = prompt("> ")
        if choice.strip().isdigit():
            idx = int(choice.strip())
            if 1 <= idx <= len(providers):
                return providers[idx - 1]
        for p in providers:
            if choice.strip() == p.name:
                return p
        print_formatted_text(Text(f"无效选择：{choice}", style="bold red"))
