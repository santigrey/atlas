"""Atlas MCP server-side telemetry mirror.

Mirrors atlas.mcp_client._log_event pattern (verified live per P6 #28): inserts
one row into atlas.events with source='atlas.mcp_server'. Falls back to
structlog.info() if no db is wired.

SECRETS DISCIPLINE: payload contains tool_name + arg_keys + status + duration_ms
+ caller_endpoint (NOT arg values). Tool argument VALUES are never persisted.

v0.2 P5 #23 will extract this + atlas.mcp_client._log_event into a shared
atlas.telemetry utility.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from atlas.db import Database

log = structlog.get_logger(__name__)

SOURCE = "atlas.mcp_server"


async def log_event(
    *, db: Database | None, kind: str, payload: dict[str, Any]
) -> None:
    """Insert one row into atlas.events with source='atlas.mcp_server'.

    Args:
        db: Database pool (or None for structlog fallback).
        kind: Event kind: 'tool_call' | 'tool_call_denied' | 'tool_call_error' | 'tools_list'.
        payload: dict of tool_name + arg_keys + status + duration_ms + caller_endpoint
            (+ deny_reason / error_type for the negative cases). NEVER arg values.
    """
    if db is None:
        log.info("mcp_server_event", kind=kind, **payload)
        return
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO atlas.events (source, kind, payload) "
                "VALUES (%s, %s, %s::jsonb)",
                (SOURCE, kind, json.dumps(payload, default=str)),
            )
            await conn.commit()
