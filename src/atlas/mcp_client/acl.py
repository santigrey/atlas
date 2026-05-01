"""Atlas MCP client-side ACL: deny-list patterns enforced before network call.

v0.1 scope: deny patterns evaluated against tool arguments. If matched, raise
AtlasAclDenied BEFORE the network call so the server never sees the request.

Server-side ACL is v0.2 P5 (separate concern, not addressed here).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class AtlasAclDenied(Exception):
    """Raised when a tool call matches a client-side ACL deny pattern."""


@dataclass(frozen=True)
class AclDenyPattern:
    """A single client-side ACL deny rule.

    Matches when tool_name equals the call's tool name AND the named arg's
    value (coerced to str) matches the compiled pattern.
    """

    tool_name: str
    arg: str
    pattern: re.Pattern  # compiled regex
    reason: str


ACL_DENY_PATTERNS: list[AclDenyPattern] = [
    AclDenyPattern(
        tool_name="homelab_file_write",
        arg="path",
        pattern=re.compile(r"^/home/jes/control-plane/"),
        reason="Atlas not authorized to write to control-plane repo (CEO/PD only)",
    ),
]


def check_acl(tool_name: str, arguments: dict) -> None:
    """Raise AtlasAclDenied if any ACL_DENY_PATTERNS rule matches the call.

    Args:
        tool_name: MCP tool name (e.g. 'homelab_ssh_run').
        arguments: Tool arguments dict.

    Raises:
        AtlasAclDenied: If any deny pattern matches. Caller must NOT proceed
            to the network call. Tool argument VALUES never appear in the
            exception message beyond the regex pattern itself.
    """
    for p in ACL_DENY_PATTERNS:
        if p.tool_name != tool_name:
            continue
        # Look in top-level args AND nested 'params' (handles auto-wrapped form
        # per Option B Refinement 2, ratified commit 6eaab4e).
        val = arguments.get(p.arg)
        if val is None and isinstance(arguments.get("params"), dict):
            val = arguments["params"].get(p.arg)
        if val is None:
            continue
        if p.pattern.search(str(val)):
            raise AtlasAclDenied(
                f"ACL deny: tool={tool_name} arg={p.arg} matches "
                f"{p.pattern.pattern!r}; reason: {p.reason}"
            )
