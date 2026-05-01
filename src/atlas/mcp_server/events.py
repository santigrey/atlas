"""atlas_events_search implementation.

Reads atlas.events with optional source / kind / ts_after / ts_before filter,
order by ts DESC, limit per params.limit.
"""

from __future__ import annotations

from typing import Any

from atlas.db import Database
from atlas.mcp_server.inputs import EventsSearchInput


async def search_events(params: EventsSearchInput, db: Database) -> list[dict[str, Any]]:
    """Search atlas.events with optional filters.

    Returns list of dicts {id, ts, source, kind, payload}, ordered by ts DESC.
    """
    sql_parts: list[str] = [
        "SELECT id, ts, source, kind, payload FROM atlas.events WHERE 1=1"
    ]
    args: list[Any] = []

    if params.source is not None:
        sql_parts.append("AND source = %s")
        args.append(params.source)
    if params.kind is not None:
        sql_parts.append("AND kind = %s")
        args.append(params.kind)
    if params.ts_after is not None:
        sql_parts.append("AND ts >= %s")
        args.append(params.ts_after)
    if params.ts_before is not None:
        sql_parts.append("AND ts <= %s")
        args.append(params.ts_before)

    sql_parts.append("ORDER BY ts DESC LIMIT %s")
    args.append(params.limit)

    sql = " ".join(sql_parts)

    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            rows = await cur.fetchall()

    return [
        {
            "id": r[0],
            "ts": r[1],
            "source": r[2],
            "kind": r[3],
            "payload": r[4],
        }
        for r in rows
    ]
