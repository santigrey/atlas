"""atlas.tasks state machine implementations (Cycle 1I).

v0.1 implements 4 legal transitions:
- null -> pending     (create_task)
- pending -> running  (claim_task; FOR UPDATE SKIP LOCKED race-safe; owner=caller_endpoint)
- running -> done     (complete_task; owner-equality required)
- running -> failed   (fail_task; owner-equality required)

Deferred to v0.2 (per v0.2 P5 #29-#34):
- cancel (pending -> failed)
- resurrect/retry (* -> pending)
- free update_status
- auth-context-beyond-tailnet (replaces caller_endpoint owner with structured agent identity)
- row-level visibility on list/get
- DB updated_at trigger
- Structured FailureResult type
- Worker heartbeat / claim-timeout / dead-letter

Note: owner field at v0.1 is caller_endpoint (X-Real-IP from nginx; matches
Cycle 1H telemetry). v0.2 P5 #30 will replace with structured agent/user identity.

DB API: uses atlas.db.Database psycopg-style API verified live per P6 #29:
- async with db.connection() as conn: async with conn.cursor() as cur:
- await cur.execute(sql, args) with %s placeholders
- await cur.fetchall() / await cur.fetchone() returns tuples (no row_factory set)
- await conn.commit() after writes
"""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from atlas.db import Database
from atlas.mcp_server.errors import AtlasTaskStateError
from atlas.mcp_server.inputs import (
    TasksClaimInput,
    TasksCompleteInput,
    TasksCreateInput,
    TasksFailInput,
    TasksGetInput,
    TasksListInput,
)

# Canonical column order used by all RETURNING / SELECT statements below.
# Tuple indices: 0=id, 1=status, 2=owner, 3=payload, 4=result, 5=created_at, 6=updated_at
_COLS = "id, status, owner, payload, result, created_at, updated_at"


def _row_to_dict(row: tuple) -> dict[str, Any]:
    """Normalize a 7-column psycopg tuple row into a serializable dict."""
    return {
        "id": str(row[0]),
        "status": row[1],
        "owner": row[2],
        "payload": row[3],
        "result": row[4],
        "created_at": row[5],
        "updated_at": row[6],
    }


async def create_task(params: TasksCreateInput, db: Database) -> dict[str, Any]:
    """INSERT INTO atlas.tasks (status='pending', owner=NULL, payload).

    Returns the created row as dict.
    """
    payload_json: Optional[str] = (
        json.dumps(params.payload, default=str) if params.payload is not None else None
    )
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO atlas.tasks (status, payload) "
                f"VALUES ('pending', %s::jsonb) RETURNING {_COLS}",
                (payload_json,),
            )
            row = await cur.fetchone()
            await conn.commit()
    if row is None:
        raise RuntimeError("INSERT INTO atlas.tasks returned no row")
    return _row_to_dict(row)


async def list_tasks(params: TasksListInput, db: Database) -> list[dict[str, Any]]:
    """SELECT FROM atlas.tasks WHERE optional filters ORDER BY created_at DESC LIMIT N."""
    sql_parts: list[str] = [f"SELECT {_COLS} FROM atlas.tasks WHERE 1=1"]
    args: list[Any] = []
    if params.status is not None:
        sql_parts.append("AND status = %s")
        args.append(params.status)
    if params.owner is not None:
        sql_parts.append("AND owner = %s")
        args.append(params.owner)
    if params.created_after is not None:
        sql_parts.append("AND created_at >= %s")
        args.append(params.created_after)
    if params.created_before is not None:
        sql_parts.append("AND created_at <= %s")
        args.append(params.created_before)
    sql_parts.append("ORDER BY created_at DESC LIMIT %s")
    args.append(params.limit)
    sql = " ".join(sql_parts)
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_task(params: TasksGetInput, db: Database) -> Optional[dict[str, Any]]:
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {_COLS} FROM atlas.tasks WHERE id = %s",
                (params.id,),
            )
            row = await cur.fetchone()
    return _row_to_dict(row) if row is not None else None


async def claim_task(
    params: TasksClaimInput, db: Database, caller_endpoint: str
) -> Optional[dict[str, Any]]:
    """Atomic pending->running with FOR UPDATE SKIP LOCKED race-safety.

    Returns claimed row OR None (queue empty matching filter).
    Owner is set to caller_endpoint (X-Real-IP from nginx).
    """
    if params.payload_kind is not None:
        sql = (
            "UPDATE atlas.tasks SET status='running', owner=%s, updated_at=now() "
            "WHERE id = ("
            "  SELECT id FROM atlas.tasks "
            "  WHERE status='pending' AND payload->>'kind' = %s "
            "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ") "
            f"RETURNING {_COLS}"
        )
        sql_args: tuple = (caller_endpoint, params.payload_kind)
    else:
        sql = (
            "UPDATE atlas.tasks SET status='running', owner=%s, updated_at=now() "
            "WHERE id = ("
            "  SELECT id FROM atlas.tasks "
            "  WHERE status='pending' "
            "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ") "
            f"RETURNING {_COLS}"
        )
        sql_args = (caller_endpoint,)
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, sql_args)
            row = await cur.fetchone()
            await conn.commit()
    return _row_to_dict(row) if row is not None else None


async def complete_task(
    params: TasksCompleteInput, db: Database, caller_endpoint: str
) -> dict[str, Any]:
    """running -> done with strict owner-equality. ERROR on terminal-state or wrong-owner."""
    return await _terminal_transition(
        task_id=params.id,
        result=params.result,
        db=db,
        caller_endpoint=caller_endpoint,
        new_status="done",
    )


async def fail_task(
    params: TasksFailInput, db: Database, caller_endpoint: str
) -> dict[str, Any]:
    """running -> failed with strict owner-equality. ERROR on terminal-state or wrong-owner."""
    return await _terminal_transition(
        task_id=params.id,
        result=params.result,
        db=db,
        caller_endpoint=caller_endpoint,
        new_status="failed",
    )


async def _terminal_transition(
    *,
    task_id: UUID,
    result: dict,
    db: Database,
    caller_endpoint: str,
    new_status: str,
) -> dict[str, Any]:
    """Shared running -> {done, failed} transition with disambiguating error handling.

    On 0-row UPDATE: re-query the task to disambiguate among:
    - not_found      (task does not exist at all)
    - wrong_status   (task is in some non-running state)
    - wrong_owner    (task is running but owned by a different caller)
    - race           (task moved between UPDATE and disambiguation SELECT)
    """
    result_json = json.dumps(result, default=str)
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE atlas.tasks SET status=%s, result=%s::jsonb, updated_at=now() "
                "WHERE id=%s AND owner=%s AND status='running' "
                f"RETURNING {_COLS}",
                (new_status, result_json, task_id, caller_endpoint),
            )
            updated = await cur.fetchone()
            if updated is not None:
                await conn.commit()
                return _row_to_dict(updated)
            # 0-row UPDATE -- disambiguate
            await cur.execute(
                "SELECT id, status, owner FROM atlas.tasks WHERE id=%s",
                (task_id,),
            )
            diag = await cur.fetchone()
    if diag is None:
        raise AtlasTaskStateError(
            kind="not_found",
            message="task does not exist",
            task_id=str(task_id),
        )
    diag_status = diag[1]
    diag_owner = diag[2]
    if diag_status != "running":
        raise AtlasTaskStateError(
            kind="wrong_status",
            message=(
                f"task is in terminal/non-running state '{diag_status}'; "
                f"cannot transition to '{new_status}'"
            ),
            task_id=str(task_id),
            current_status=diag_status,
            expected_status="running",
        )
    if diag_owner != caller_endpoint:
        raise AtlasTaskStateError(
            kind="wrong_owner",
            message="task is owned by a different caller",
            task_id=str(task_id),
            actual_owner=diag_owner,
            caller_endpoint=caller_endpoint,
        )
    raise AtlasTaskStateError(
        kind="race",
        message="transient race between UPDATE and disambiguation SELECT; retry the call",
        task_id=str(task_id),
    )
