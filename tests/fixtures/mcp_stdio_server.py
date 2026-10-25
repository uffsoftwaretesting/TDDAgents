"""A tiny MCP server over stdio, spawned by `tests/test_mcp_transports.py`."""

import os
import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("stdio-demo", instructions=os.environ.get("DEMO_INSTRUCTIONS"))


@server.tool()
def echo(text: str) -> str:
    """Echo the text."""
    return text


@server.tool()
def env(name: str) -> str:
    """One environment variable of the server process."""
    return os.environ.get(name, "<unset>")


if __name__ == "__main__":
    if "--fail" in sys.argv:
        print("boom on stderr", file=sys.stderr)
        sys.exit(3)
    server.run()
