"""Atlas inbound MCP server (Cycle 1G skeleton).

FastMCP-based listener bound to 127.0.0.1:8001. nginx on Beast :8443 fronts
this with the Tailscale-issued FQDN cert at /etc/ssl/tailscale/.

Cycle 1G ships the bare listener that proves the TLS+nginx+FastMCP+systemd
chain works end-to-end. NO @mcp.tool definitions yet; tools_count=0 in
smoke is the expected and correct outcome.

Tool surface (atlas.events search, atlas.embeddings upsert/query,
atlas.inference history, etc.) lands in a subsequent paco_request.
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("atlas-mcp")

# NO @mcp.tool definitions yet -- tool surface is the next paco_request.


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("FASTMCP_PORT", "8001"))
    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=port)
