"""Polls atlas.tasks for pending rows; claims via SKIP LOCKED; executes; writes results.

Per P6 #29 + Cycle 1I canonical pattern (atlas.mcp_server.tasks.claim_task).
Spec amended Day 78 morning per docs/paco_response_atlas_v0_1_phase2_db_api_amendment.md
to correct 5 directive-author errors: (1) get_pool->Database, (2) asyncpg->psycopg API,
(3) started_at->updated_at, (4) completed_at->updated_at, (5) RETURNING column set
+ payload.kind extraction (kind lives inside payload jsonb, no top-level column).
"""
import asyncio
import json
import logging
from atlas.db import Database

log = logging.getLogger(__name__)


async def task_poller():
    db = Database()
    while True:
        # Claim one pending task via FOR UPDATE SKIP LOCKED (Cycle 1I state machine)
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE atlas.tasks SET status='running', updated_at=now() "
                    "WHERE id = ("
                    "  SELECT id FROM atlas.tasks "
                    "  WHERE status='pending' "
                    "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                    ") "
                    "RETURNING id, payload"
                )
                row = await cur.fetchone()
                await conn.commit()
        if row is None:
            await asyncio.sleep(5)  # 5-second cadence per Pick 2
            continue
        task_id, payload = row[0], row[1]
        kind = payload.get("kind") if isinstance(payload, dict) else None
        log.info(f'Claimed task {task_id} payload_kind={kind}')
        if kind == "deebo_dispatch":
            from atlas.agent.domains.deebo import execute_deebo_task
            try:
                await execute_deebo_task(task_id, payload, db)
            except Exception as e:
                log.exception(f"deebo_dispatch task {task_id} crashed: {e}")
                async with db.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE atlas.tasks SET status='failed', result=%s, updated_at=now() WHERE id=%s",
                            (json.dumps({"error": f"poller crash: {type(e).__name__}: {str(e)[:300]}"}), task_id),
                        )
                        await conn.commit()
        else:
            # existing no-op fall-through (preserves current behavior for any other producer)
            async with db.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE atlas.tasks SET status='done', updated_at=now() WHERE id=%s",
                        (task_id,)
                    )
                    await conn.commit()
