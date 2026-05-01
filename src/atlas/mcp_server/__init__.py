"""Atlas MCP server (inbound).

FastMCP-based MCP server exposing Atlas tools to external clients.
Loopback-bound (127.0.0.1:8001); fronted by Beast nginx :8443 with
Tailscale-issued FQDN cert.

Cycle 1G ships the bare listener (no @mcp.tool definitions). Tool
surface lands in subsequent paco_request.

Note: server module is NOT auto-imported at package import time, to
avoid `python -m atlas.mcp_server.server` re-import RuntimeWarning.
"""
