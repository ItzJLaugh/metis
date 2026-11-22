"""Metis MCP server: the one tool Claude calls. Claude Desktop starts this itself once you press Connect in the app."""

import threading
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from metis_mcp import core

mcp = MCPServer(
    name="Metis",
    instructions="When the user's prompt may depend on their own files, call `request` with the prompt. "
    "Metis returns only the part of the file that matters and the file's absolute path.",
    version="0.3.0",
)

# Send the user's prompt [unchanged]
@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True), structured_output=True)
def request(prompt: str) -> dict[str, Any]:
    """Find the excerpts of the user's files that help answer this prompt, each with the file's absolute path.
    When several excerpts score almost the same (up to 5), all are returned; use the one that answers the prompt."""
    return core.retrieve(prompt, log=True)


if __name__ == "__main__":
    threading.Thread(target=core.agent, daemon=True).start()  # load Laya now, so the first search doesn't wait
    core.start_idle_unloader()  # frees the models after a quiet spell
    mcp.run()  # stdio: Claude Desktop talks to Metis through this process's input and output
