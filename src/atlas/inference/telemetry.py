"""Convert Ollama response dict to atlas.events row.

Durations stored in MILLISECONDS (converted from Ollama nanosecond convention).
No prompt/response content captured -- telemetry only.
"""

from __future__ import annotations

import json
from typing import Any

from atlas.db import Database
from atlas.inference.models import InferenceTelemetry


def _ns_to_ms(ns: int | None) -> float | None:
    """Convert nanoseconds to milliseconds, rounded to 3 decimals."""
    if ns is None:
        return None
    return round(ns / 1_000_000, 3)


def build_telemetry(
    response_dict: dict[str, Any],
    *,
    fallback_chain: list[str],
    endpoint: str,
    status: str = "success",
    error: str | None = None,
) -> InferenceTelemetry:
    """Build telemetry from raw Ollama response (or final streaming chunk)."""
    return InferenceTelemetry(
        model=response_dict.get("model", ""),
        prompt_eval_count=response_dict.get("prompt_eval_count"),
        eval_count=response_dict.get("eval_count"),
        total_duration_ms=_ns_to_ms(response_dict.get("total_duration")),
        load_duration_ms=_ns_to_ms(response_dict.get("load_duration")),
        prompt_eval_duration_ms=_ns_to_ms(response_dict.get("prompt_eval_duration")),
        eval_duration_ms=_ns_to_ms(response_dict.get("eval_duration")),
        status=status,
        fallback_chain=fallback_chain,
        endpoint=endpoint,
        error=error,
    )


async def log_inference_event(
    db: Database,
    *,
    kind: str,  # 'generate' | 'chat' | 'stream_generate' | 'stream_chat'
    telemetry: InferenceTelemetry,
) -> None:
    """Insert one row into atlas.events with source='atlas.inference'."""
    payload = telemetry.model_dump(exclude_none=False)
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO atlas.events (source, kind, payload) VALUES (%s, %s, %s::jsonb)",
                ("atlas.inference", kind, json.dumps(payload, default=str)),
            )
            await conn.commit()
