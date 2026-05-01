"""Pydantic input classes for atlas-mcp inbound MCP tools (Cycles 1H + 1I).

Mirrors CK mcp_server.py BaseModel + Field validator pattern (verified live
per P6 #28). Field constraints encode declarative allow-list semantics; deny
patterns live in acl.py as defense-in-depth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Source allowlist for events search filter (Cycle 1H)
EVENTS_SOURCE_ALLOWLIST = {
    "atlas.embeddings",
    "atlas.inference",
    "atlas.mcp_client",
    "atlas.mcp_server",
}

# Status allowlist for tasks list filter (Cycle 1I)
TASK_STATUSES = {"pending", "running", "done", "failed"}

# jsonb size caps (Cycle 1I -- per Paco ruling, 50KB)
MAX_PAYLOAD_BYTES = 50_000
MAX_RESULT_BYTES = 50_000


def _jsonb_size_cap(field_name: str, max_bytes: int):
    """Reusable validator factory for jsonb size caps."""
    import json as _json

    def _validate(v):
        if v is None:
            return v
        size = len(_json.dumps(v, default=str))
        if size > max_bytes:
            raise ValueError(
                f"{field_name} serialized form ({size} bytes) exceeds {max_bytes}-byte cap"
            )
        return v

    return _validate


# =============================================================================
# Cycle 1H -- 4 input classes
# =============================================================================


class EventsSearchInput(BaseModel):
    """atlas_events_search: filter atlas.events by source / kind / ts range."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: Optional[str] = Field(
        default=None,
        description="Source filter (allowlist of atlas.* sources)",
        max_length=50,
    )
    kind: Optional[str] = Field(default=None, description="Kind filter", max_length=50)
    ts_after: Optional[datetime] = Field(default=None, description="Lower bound timestamp")
    ts_before: Optional[datetime] = Field(default=None, description="Upper bound timestamp")
    limit: int = Field(default=50, ge=1, le=100, description="Max rows to return")

    @field_validator("source")
    @classmethod
    def source_in_allowlist(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in EVENTS_SOURCE_ALLOWLIST:
            raise ValueError(
                f"source must be one of {sorted(EVENTS_SOURCE_ALLOWLIST)} or None"
            )
        return v


class MemoryQueryInput(BaseModel):
    """atlas_memory_query: server embeds query_text and finds nearest neighbors."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query_text: str = Field(
        ...,
        description="Query text (server embeds and finds nearest neighbors)",
        min_length=1,
        max_length=10_000,
    )
    top_k: int = Field(default=5, ge=1, le=20, description="Number of results to return")
    kind: Optional[str] = Field(
        default=None, description="Filter by memory kind", max_length=50
    )


class MemoryUpsertInput(BaseModel):
    """atlas_memory_upsert: server embeds content and INSERTs into atlas.memory."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(
        ...,
        description="Memory kind label",
        min_length=1,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    content: str = Field(
        ...,
        description="Memory text content",
        min_length=1,
        max_length=100_000,
    )
    metadata: Optional[dict] = Field(
        default=None, description="Optional metadata dict (max 10KB serialized)"
    )

    @field_validator("metadata")
    @classmethod
    def metadata_size_cap(cls, v: Optional[dict]) -> Optional[dict]:
        if v is None:
            return v
        import json as _json

        if len(_json.dumps(v, default=str)) > 10_000:
            raise ValueError("metadata serialized form exceeds 10KB cap")
        return v


class InferenceHistoryInput(BaseModel):
    """atlas_inference_history: filter atlas.events WHERE source='atlas.inference'."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model: Optional[str] = Field(
        default=None, description="Model name filter", max_length=200
    )
    ts_after: Optional[datetime] = Field(
        default=None,
        description="Lower bound timestamp (default: now() - 7 days if both bounds None)",
    )
    ts_before: Optional[datetime] = Field(default=None, description="Upper bound timestamp")
    limit: int = Field(default=20, ge=1, le=50, description="Max rows to return")


# =============================================================================
# Cycle 1I -- 6 input classes for atlas.tasks state machine
# =============================================================================


class TasksCreateInput(BaseModel):
    """atlas_tasks_create: INSERT new pending row with optional payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    payload: Optional[dict] = Field(
        default=None, description="Task payload jsonb (max 50KB serialized)"
    )

    @field_validator("payload")
    @classmethod
    def payload_size_cap(cls, v):
        return _jsonb_size_cap("payload", MAX_PAYLOAD_BYTES)(v)


class TasksListInput(BaseModel):
    """atlas_tasks_list: filter atlas.tasks by status / owner / created_at range."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Filter by status (pending/running/done/failed)",
    )
    owner: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Filter by owner (typically X-Real-IP at v0.1)",
    )
    created_after: Optional[datetime] = Field(default=None, description="Lower bound timestamp")
    created_before: Optional[datetime] = Field(default=None, description="Upper bound timestamp")
    limit: int = Field(default=50, ge=1, le=200, description="Max rows to return")

    @field_validator("status")
    @classmethod
    def status_in_allowlist(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in TASK_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(TASK_STATUSES)} or None"
            )
        return v


class TasksGetInput(BaseModel):
    """atlas_tasks_get: single task by uuid."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID = Field(..., description="Task UUID")


class TasksClaimInput(BaseModel):
    """atlas_tasks_claim: atomic pending->running transition; owner=caller_endpoint.

    caller_endpoint is NOT in this input -- always derived from X-Real-IP via
    FastMCP Context (mirror Cycle 1H caller_endpoint extraction).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    payload_kind: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Optional filter on payload->>'kind'",
    )


class TasksCompleteInput(BaseModel):
    """atlas_tasks_complete: running->done transition; owner-equality required."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID = Field(..., description="Task UUID to complete")
    result: dict = Field(
        ...,
        description="Required result jsonb on success (max 50KB serialized)",
    )

    @field_validator("result")
    @classmethod
    def result_size_cap(cls, v):
        return _jsonb_size_cap("result", MAX_RESULT_BYTES)(v)


class TasksFailInput(BaseModel):
    """atlas_tasks_fail: running->failed transition; owner-equality required.

    Recommended convention for `result`: {error_type, error_message, traceback}.
    Convention is NOT enforced at v0.1 (any Pydantic-validated dict accepted);
    structured FailureResult type ratification is v0.2 P5 #33.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID = Field(..., description="Task UUID to fail")
    result: dict = Field(
        ...,
        description="Required failure result; recommended convention: {error_type, error_message, traceback}",
    )

    @field_validator("result")
    @classmethod
    def result_size_cap(cls, v):
        return _jsonb_size_cap("result", MAX_RESULT_BYTES)(v)
