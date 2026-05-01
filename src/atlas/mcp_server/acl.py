"""Atlas MCP server-side ACL: authoritative authorization boundary for inbound traffic.

Server-side ACL is the LAST line of defense and the AUTHORITATIVE control. Inbound
clients may be Cowork bridge, future agents, or anything not running
atlas.mcp_client; server CANNOT trust any client-side ACL.

Client-side ACL (atlas.mcp_client.acl) is defense-in-depth: it reduces network
load and provides auditable client-side decisions, but server-side ACL is the
only guaranteed control.

v0.1 deny patterns may be minimal/empty -- the infrastructure is forward-compat
for future deny needs (block specific kinds, block writes from certain caller
IPs, etc.). Pydantic Field validators in inputs.py enforce per-tool allow-list
constraints declaratively at parse time.

Mirrors atlas.mcp_client.acl shape (verified live per P6 #28): same
AclDenyPattern dataclass shape, same nested-params lookup, same exception
posture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class AtlasMcpServerAclDenied(Exception):
    """Raised when an inbound tool call matches a server-side ACL deny pattern."""


@dataclass(frozen=True)
class ServerAclDenyPattern:
    """Server-side deny rule. Matches when tool_name equals AND the named arg's
    value (coerced to str) matches the compiled pattern."""

    tool_name: str
    arg: str
    pattern: re.Pattern  # compiled regex
    reason: str


ACL_DENY_PATTERNS_SERVER: list[ServerAclDenyPattern] = [
    # v0.1: minimal/empty. Pydantic Field validators in inputs.py handle
    # allow-list semantics. Populate as concrete deny needs emerge.
]


def check_server_acl(tool_name: str, arguments: dict) -> None:
    """Raise AtlasMcpServerAclDenied if any deny pattern matches.

    Looks in top-level args AND nested 'params' (handles auto-wrapped form
    per Cycle 1F Refinement 2 mirror).
    """
    for p in ACL_DENY_PATTERNS_SERVER:
        if p.tool_name != tool_name:
            continue
        # Top-level lookup
        val = arguments.get(p.arg)
        # Nested 'params' fallback (handles auto-wrapped form)
        if val is None and isinstance(arguments.get("params"), dict):
            val = arguments["params"].get(p.arg)
        if val is None:
            continue
        if p.pattern.search(str(val)):
            raise AtlasMcpServerAclDenied(
                f"ACL deny: tool={tool_name} arg={p.arg} matches "
                f"{p.pattern.pattern!r}; reason: {p.reason}"
            )
