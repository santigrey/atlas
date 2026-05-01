"""Pydantic input classes for atlas-mcp inbound MCP tools (Cycle 1H).

Mirrors CK mcp_server.py BaseModel + Field validator pattern (verified live
per P6 #28). Field constraints encode declarative allow-list semantics; deny
patterns live in acl.py as defense-in-depth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Source allowlist for events search filter
EVENTS_SOURCE_ALLOWLIST = {
    "atlas.embeddings",
    "atlas.inference",
    "atlas.mcp_client",
    "atlas.mcp_server",
}


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
