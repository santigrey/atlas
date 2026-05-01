"""Atlas MCP client gateway.

Async wrapper around mcp.ClientSession + streamablehttp_client to talk to the
homelab MCP server on CK at https://sloan3.tail1216a3.ts.net:8443/mcp.

Client-side ACL enforced before network call (server-side ACL is v0.2 P5).
Telemetry to atlas.events with source='atlas.mcp_client'; arg VALUES never
persisted (secrets discipline).

Per Phase 3 ratification (commit 77759f8) and Cycle 1F GO directive (3c9c8dd):
- DEFAULT_MCP_URL points at FQDN (cert SAN is FQDN-only)
- MCP_PROTOCOL_VERSION header required by FastMCP 1.26+ server
- /etc/hosts entry on Beast (192.168.1.10 sloan3.tail1216a3.ts.net) bridges
  FQDN cert validation with LAN routing.
"""

from atlas.mcp_client.acl import (
    ACL_DENY_PATTERNS,
    AclDenyPattern,
    AtlasAclDenied,
    check_acl,
)
from atlas.mcp_client.client import (
    DEFAULT_HEADERS,
    DEFAULT_MCP_URL,
    MCP_PROTOCOL_VERSION,
    McpClient,
    get_mcp_client,
)

__all__ = [
    "ACL_DENY_PATTERNS",
    "AclDenyPattern",
    "AtlasAclDenied",
    "DEFAULT_HEADERS",
    "DEFAULT_MCP_URL",
    "MCP_PROTOCOL_VERSION",
    "McpClient",
    "check_acl",
    "get_mcp_client",
]
