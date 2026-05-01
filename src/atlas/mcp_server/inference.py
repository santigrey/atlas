"""atlas_inference_history implementation.

Reads atlas.events WHERE source='atlas.inference' with optional model filter
and ts range. Default ts_after = now() - 7 days when both bounds are None.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from atlas.db import Database
from atlas.mcp_server.inputs import InferenceHistoryInput


async def history_inference(
    params: InferenceHistoryInput, db: Database
) -> list[dict[str, Any]]:
    """Filter atlas.events for source='atlas.inference' history.

    Returns list of dicts {id, ts, kind, payload}, ordered by ts DESC, limited
    by params.limit. If neither ts_after nor ts_before is provided, defaults
    ts_after to now() - 7 days.
    """
    sql_parts: list[str] = [
        "SELECT id, ts, kind, payload FROM atlas.events "
        "WHERE source = 'atlas.inference'"
    ]
    args: list[Any] = []

    if params.model is not None:
        sql_parts.append("AND payload->>'model' = %s")
        args.append(params.model)

    ts_after = params.ts_after
    ts_before = params.ts_before
    if ts_after is None and ts_before is None:
        ts_after = datetime.now(tz=timezone.utc) - timedelta(days=7)

    if ts_after is not None:
        sql_parts.append("AND ts >= %s")
        args.append(ts_after)
    if ts_before is not None:
        sql_parts.append("AND ts <= %s")
        args.append(ts_before)

    sql_parts.append("ORDER BY ts DESC LIMIT %s")
    args.append(params.limit)

    sql = " ".join(sql_parts)

    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            rows = await cur.fetchall()

    return [
        {"id": r[0], "ts": r[1], "kind": r[2], "payload": r[3]}
        for r in rows
    ]
