"""最小 stdio MCP server：ch07 端到端演示用（server-everything 的最小本地替代）。

启动：`python examples/mcp_demo_server.py`——由 novacode 以 stdio 方式拉起并连接。
工具：`echo`（回显文本）、`add`（两数相加）。
"""

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="demo")


@server.tool()
def echo(text: str) -> str:
    """回显文本。"""
    return f"echo: {text}"


@server.tool()
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


if __name__ == "__main__":
    server.run(transport="stdio")
