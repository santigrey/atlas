"""Atlas inbound MCP server (Cycles 1G + 1H + 1I tool surface).

FastMCP-based listener bound to 127.0.0.1:8001. nginx on Beast :8443 fronts
this with the Tailscale-issued FQDN cert at /etc/ssl/tailscale/.

Cycle 1H ships 4 read/write tools:
- atlas_events_search       (READ)
- atlas_events_create       (WRITE; thin INSERT, no Tier dispatch)
- atlas_memory_query        (READ)
- atlas_memory_upsert       (WRITE)
- atlas_inference_history   (READ)

Cycle 1I ships 6 atlas.tasks state machine tools (FINAL Cycle 1 cycle):
- atlas_tasks_create        (WRITE; null -> pending)
- atlas_tasks_list          (READ)
- atlas_tasks_get           (READ)
- atlas_tasks_claim         (WRITE; pending -> running; FOR UPDATE SKIP LOCKED race-safe)
- atlas_tasks_complete      (WRITE; running -> done; owner-equality required)
- atlas_tasks_fail          (WRITE; running -> failed; owner-equality required)

v0.1 owner-as-IP-string: tasks.owner is set to caller_endpoint (X-Real-IP from
nginx vhost). v0.2 P5 #30 will replace with structured agent/user identity.

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
from atlas.mcp_server.errors import AtlasTaskStateError
from atlas.mcp_server.events import create_event, search_events
from atlas.mcp_server.inference import history_inference
from atlas.mcp_server.inputs import (
    EventsCreateInput,
    EventsSearchInput,
    InferenceHistoryInput,
    MemoryQueryInput,
    MemoryUpsertInput,
    TasksClaimInput,
    TasksCompleteInput,
    TasksCreateInput,
    TasksFailInput,
    TasksGetInput,
    TasksListInput,
)
from atlas.mcp_server.memory import query_memory, upsert_memory
from atlas.mcp_server.tasks import (
    claim_task,
    complete_task,
    create_task,
    fail_task,
    get_task,
    list_tasks,
)
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

    For tools that DON'T need caller_endpoint in the body (Cycle 1H tools +
    atlas_tasks_create/list/get).

    P6 #27 invariant: caller_arg_keys captured BEFORE any internal transformation.
    SECRETS DISCIPLINE: arg VALUES never persisted; only the keys.
    """
    args_dump = params.model_dump()
    caller_arg_keys = sorted(args_dump.keys())
    caller_endpoint = _extract_caller_endpoint(ctx)
    db = _get_db()

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


async def _wrap_tool_with_endpoint(
    name: str,
    params: Any,
    ctx: Context,
    body: Callable[[Any, Database, str], Awaitable[Any]],
) -> Any:
    """Like _wrap_tool but body receives caller_endpoint as 3rd arg.

    Used by atlas_tasks_claim / atlas_tasks_complete / atlas_tasks_fail which
    need owner enforcement at the SQL layer.

    Also captures AtlasTaskStateError.kind in tool_call_error telemetry payload
    (error_kind discriminator) for actionable observability.
    """
    args_dump = params.model_dump()
    caller_arg_keys = sorted(args_dump.keys())
    caller_endpoint = _extract_caller_endpoint(ctx)
    db = _get_db()

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
        result = await body(params, db, caller_endpoint)
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
    except AtlasTaskStateError as e:
        await log_event(
            db=db,
            kind="tool_call_error",
            payload={
                "tool_name": name,
                "arg_keys": caller_arg_keys,
                "status": "error",
                "error_type": "AtlasTaskStateError",
                "error_kind": e.kind,
                "duration_ms": _ns_to_ms(time.perf_counter_ns() - t0),
                "caller_endpoint": caller_endpoint,
            },
        )
        raise
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


# =============================================================================
# Cycle 1H tools (4)
# =============================================================================


@mcp.tool(
    name="atlas_events_search",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def atlas_events_search(params: EventsSearchInput, ctx: Context) -> str:
    """Search atlas.events by source / kind / ts range. Returns rows ordered by ts DESC."""
    rows = await _wrap_tool("atlas_events_search", params, ctx, search_events)
    return json.dumps(rows, default=str, indent=2)


@mcp.tool(
    name="atlas_events_create",
    description="INSERT one row into atlas.events. Thin write; no Tier dispatch."
)
async def atlas_events_create(params: EventsCreateInput, ctx: Context) -> str:
    """MCP wrapper for create_event."""
    result = await _wrap_tool("atlas_events_create", params, ctx, create_event)
    return json.dumps(result, default=str)


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


# =============================================================================
# Cycle 1I tools (6) -- atlas.tasks state machine
# =============================================================================


@mcp.tool(
    name="atlas_tasks_create",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def atlas_tasks_create(params: TasksCreateInput, ctx: Context) -> str:
    """Create new atlas.tasks row in 'pending' state. owner=NULL initially."""
    row = await _wrap_tool("atlas_tasks_create", params, ctx, create_task)
    return json.dumps(row, default=str, indent=2)


@mcp.tool(
    name="atlas_tasks_list",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def atlas_tasks_list(params: TasksListInput, ctx: Context) -> str:
    """List atlas.tasks with optional status / owner / created_at filters."""
    rows = await _wrap_tool("atlas_tasks_list", params, ctx, list_tasks)
    return json.dumps(rows, default=str, indent=2)


@mcp.tool(
    name="atlas_tasks_get",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def atlas_tasks_get(params: TasksGetInput, ctx: Context) -> str:
    """Get a single atlas.tasks row by uuid."""
    row = await _wrap_tool("atlas_tasks_get", params, ctx, get_task)
    return json.dumps(row, default=str, indent=2)


@mcp.tool(
    name="atlas_tasks_claim",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def atlas_tasks_claim(params: TasksClaimInput, ctx: Context) -> str:
    """Atomic pending->running. owner=caller_endpoint (X-Real-IP). FOR UPDATE SKIP LOCKED.

    v0.1 owner is the tailnet caller IP. v0.2 P5 #30 will replace with structured
    agent/user identity.
    """
    row = await _wrap_tool_with_endpoint(
        "atlas_tasks_claim", params, ctx, claim_task
    )
    return json.dumps(row, default=str, indent=2)


@mcp.tool(
    name="atlas_tasks_complete",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def atlas_tasks_complete(params: TasksCompleteInput, ctx: Context) -> str:
    """running->done. Owner-equality required. ERROR on terminal/wrong-owner."""
    row = await _wrap_tool_with_endpoint(
        "atlas_tasks_complete", params, ctx, complete_task
    )
    return json.dumps(row, default=str, indent=2)


@mcp.tool(
    name="atlas_tasks_fail",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def atlas_tasks_fail(params: TasksFailInput, ctx: Context) -> str:
    """running->failed. Owner-equality required. ERROR on terminal/wrong-owner."""
    row = await _wrap_tool_with_endpoint(
        "atlas_tasks_fail", params, ctx, fail_task
    )
    return json.dumps(row, default=str, indent=2)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("FASTMCP_PORT", "8001"))
    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=port)
