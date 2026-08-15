"""MCP 客户端子包：配置加载、连接管理、工具适配。

对外接口：`Config` / `ServerConfig` / `Manager` / `McpTool` / `load_config` /
`new_manager`。仅依赖 `novacode.tool`、mcp SDK 与标准库（依赖方向无环）。
"""

from .config import Config, ServerConfig, load_config
from .manager import Manager, new_manager
from .tool import McpTool

__all__ = [
    "Config",
    "Manager",
    "McpTool",
    "ServerConfig",
    "load_config",
    "new_manager",
]
