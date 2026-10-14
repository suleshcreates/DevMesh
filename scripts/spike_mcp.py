"""Milestone 0 only: real stdio discovery and bounded empty-queue polling."""
import asyncio
import math

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("devmesh-bridge")


@mcp.tool()
def ping() -> dict[str, str]:
    """Verify that the DevMesh spike server is reachable."""
    return {"status": "pong"}


@mcp.tool()
async def await_next_task(agent_name: str, timeout_seconds: float = 45) -> dict[str, str]:
    """Wait up to 45 seconds. M0 has no task producer, so always returns no_task."""
    if not agent_name.strip():
        raise ValueError("agent_name must not be blank")
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("timeout_seconds must be finite and nonnegative")
    await asyncio.sleep(min(timeout_seconds, 45))
    return {"status": "no_task"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
