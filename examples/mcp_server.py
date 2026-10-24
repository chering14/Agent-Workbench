"""A small real MCP server. Run with the project's Python interpreter."""

from mcp.server import MCPServer

server = MCPServer("workbench-example")


@server.tool()
def estimate_effort(files: int, tests: int) -> dict:
    """Compute a transparent sample score, not a model-generated time estimate."""
    if files < 0 or tests < 0:
        raise ValueError("counts must be non-negative")
    return {"score": files * 2 + tests, "method": "fixed illustrative formula"}


if __name__ == "__main__":
    server.run()
