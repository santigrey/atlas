"""Atlas inbound MCP server (Cycle 1H tool surface).

FastMCP-based listener bound to 127.0.0.1:8001. nginx on Beast :8443 fronts
this with the Tailscale-issued FQDN cert at /etc/ssl/tailscale/.

Cycle 1H ships 4 @mcp.tool definitions:
- atlas_events_search   (READ)
- atlas_memory_query    (READ)
- atlas_memory_upsert   (WRITE)
- atlas_inference_history (READ)

Server-side ACL (acl.py) is the AUTHORITATIVE authorization boundary; client-
side ACL is defense-in-depth. Pydantic Field validators in inputs.py enforce
per-tool allow-list constraints declaratively at parse time.

Telemetry to atlas.events with source='atlas.mcp_server'; arg VALUES never
persisted (secrets discipline). caller_endpoint extracted from nginx X-Real-IP
header (Cycle 1G vhost propagation), with 'loopback' fallback if FastMCP
Context API doesn't expose request headers (v0.2 P5 #25 covers cleaner path).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP

from atlas.db import Database
from atlas.mcp_server.acl import AtlasMcpServerAclDenied, check_server_acl
from atlas.mcp_server.events import search_events
from atlas.mcp_server.inference import history_inference
from atlas.mcp_server.inputs import (
    EventsSearchInput,
    InferenceHistoryInput,
    MemoryQueryInput,
    MemoryUpsertInput,
)
from atlas.mcp_server.memory import query_memory, upsert_memory
from atlas.mcp_server.telemetry import log_event

mcp = FastMCP("atlas-mcp")

# Module-level lazy DB (one pool per process; opens on first use)
_db: Database | None = None


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def _ns_to_ms(ns: int) -> float:
    return round(ns / 1_000_000, 3)


def _extract_caller_endpoint(ctx: Context) -> str:
    """Extract X-Real-IP from nginx-forwarded request headers.

    Cycle 1G nginx vhost on Beast sets `proxy_set_header X-Real-IP $remote_addr`,
    so uvicorn sees the original tailnet caller IP in this header. Fall back
    to 'loopback' if FastMCP Context API path doesn't expose request headers
    (v0.2 P5 #25).
    """
    try:
        # FastMCP Context.request_context.request.headers (Starlette Request)
        headers = ctx.request_context.request.headers  # type: ignore[union-attr,attr-defined]
        # Starlette Headers class is case-insensitive
        return headers.get("x-real-ip") or headers.get("X-Real-IP") or "unknown"
    except (AttributeError, KeyError, TypeError):
        return "loopback"


async def _wrap_tool(
    name: str,
    params: Any,
    ctx: Context,
    body: Callable[[Any, Database], Awaitable[Any]],
) -> Any:
    """Common dispatch: capture caller_arg_keys -> ACL check -> body -> telemetry.

    P6 #27 invariant: caller_arg_keys captured BEFORE any internal transformation.
    SECRETS DISCIPLINE: arg VALUES never persisted; only the keys.
    """
    # P6 #27: capture caller-provided keys BEFORE any internal transformation.
    args_dump = params.model_dump()
    caller_arg_keys = sorted(args_dump.keys())
    caller_endpoint = _extract_caller_endpoint(ctx)
    db = _get_db()

    # Server-side ACL is the AUTHORITATIVE authorization boundary.
    try:
        check_server_acl(name, args_dump)
    except AtlasMcpServerAclDenied as e:
        await log_event(
            db=db,
            kind="tool_call_denied",
            payload={
                "tool_name": name,
                "arg_keys": caller_arg_keys,
                "status": "denied",
                "deny_reason": str(e)[:200],
                "deny_layer": "server",
                "caller_endpoint": caller_endpoint,
            },
        )
        raise

    t0 = time.perf_counter_ns()
    try:
        result = await body(params, db)
        await log_event(
            db=db,
            kind="tool_call",
            payload={
                "tool_name": name,
                "arg_keys": caller_arg_keys,
                "status": "success",
                "duration_ms": _ns_to_ms(time.perf_counter_ns() - t0),
                "caller_endpoint": caller_endpoint,
            },
        )
        return result
    except Exception as e:
        await log_event(
            db=db,
            kind="tool_call_error",
            payload={
                "tool_name": name,
                "arg_keys": caller_arg_keys,
                "status": "error",
                "error_type": type(e).__name__,
                "duration_ms": _ns_to_ms(time.perf_counter_ns() - t0),
                "caller_endpoint": caller_endpoint,
            },
        )
        raise


@mcp.tool(
    name="atlas_events_search",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def atlas_events_search(params: EventsSearchInput, ctx: Context) -> str:
    """Search atlas.events by source / kind / ts range. Returns rows ordered by ts DESC."""
    rows = await _wrap_tool("atlas_events_search", params, ctx, search_events)
    return json.dumps(rows, default=str, indent=2)


@mcp.tool(
    name="atlas_memory_query",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def atlas_memory_query(params: MemoryQueryInput, ctx: Context) -> str:
    """Vector similarity search against atlas.memory. Server embeds query_text."""
    rows = await _wrap_tool("atlas_memory_query", params, ctx, query_memory)
    return json.dumps(rows, default=str, indent=2)


@mcp.tool(
    name="atlas_memory_upsert",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def atlas_memory_upsert(params: MemoryUpsertInput, ctx: Context) -> str:
    """INSERT one row into atlas.memory; server generates the embedding."""
    result = await _wrap_tool("atlas_memory_upsert", params, ctx, upsert_memory)
    return json.dumps(result, default=str, indent=2)


@mcp.tool(
    name="atlas_inference_history",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def atlas_inference_history(
    params: InferenceHistoryInput, ctx: Context
) -> str:
    """Filter atlas.events for source='atlas.inference' history."""
    rows = await _wrap_tool(
        "atlas_inference_history", params, ctx, history_inference
    )
    return json.dumps(rows, default=str, indent=2)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("FASTMCP_PORT", "8001"))
    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=port)
