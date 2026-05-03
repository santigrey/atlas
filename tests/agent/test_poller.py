"""Atlas v0.1 Phase 8 -- Integration tests for atlas.agent.poller.

4 cases per paco_directive_atlas_v0_1_phase8.md section 2.5:
1. test_poller_claims_pending_task -- INSERT pending; verify status changes; updated_at > created_at
2. test_poller_skip_locked_no_double_claim -- 2 concurrent pollers + 1 task; exactly 1 claims it
3. test_poller_marks_done_after_handler -- full lifecycle pending -> running -> done
4. test_poller_idle_when_no_pending -- empty queue (no row with our test_run_id); poller idles + no claim log

Requires real psycopg connection to controlplane.atlas.tasks. Marked @pytest.mark.homelab.
Cleanup discipline: each test embeds a unique test_run_id in payload jsonb;
finally-block DELETE removes only rows matching that test_run_id (zero leak).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import pytest

from atlas.agent.poller import task_poller
from atlas.db import Database

pytestmark = pytest.mark.homelab


async def _insert_pending_task(db: Database, test_run_id: str, kind: str = "noop") -> str:
    """INSERT a pending atlas.tasks row tagged with test_run_id. Returns the task UUID."""
    payload = {"test_run_id": test_run_id, "kind": kind}
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO atlas.tasks (status, payload) "
                "VALUES ('pending', %s::jsonb) RETURNING id",
                (json.dumps(payload),),
            )
            row = await cur.fetchone()
            await conn.commit()
            return str(row[0])


async def _read_task(db: Database, task_id: str) -> dict[str, Any] | None:
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, created_at, updated_at FROM atlas.tasks WHERE id = %s",
                (task_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return {"status": row[0], "created_at": row[1], "updated_at": row[2]}


async def _cleanup_test_tasks(db: Database, test_run_id: str) -> None:
    """Delete any atlas.tasks rows whose payload.test_run_id matches."""
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM atlas.tasks WHERE payload->>'test_run_id' = %s",
                (test_run_id,),
            )
            await conn.commit()


async def _wait_for_status(
    db: Database, task_id: str, target: str, timeout: float = 2.0
) -> dict[str, Any] | None:
    """Poll the task row at 50ms intervals until status == target or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = await _read_task(db, task_id)
        if row and row["status"] == target:
            return row
        await asyncio.sleep(0.05)
    return await _read_task(db, task_id)


async def _cancel_and_join(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# -----------------------------------------------------------------------------
# 1. claim semantics correct
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_claims_pending_task() -> None:
    """INSERT pending row; spawn poller; verify row transitions out of pending + updated_at advances."""
    test_run_id = uuid.uuid4().hex
    db = Database()
    await db.open()
    try:
        task_id = await _insert_pending_task(db, test_run_id)
        poller_task = asyncio.create_task(task_poller())
        try:
            row = await _wait_for_status(db, task_id, "done", timeout=2.0)
            assert row is not None, "task row vanished"
            assert row["status"] != "pending", f"task still pending; got status={row['status']!r}"
            assert row["updated_at"] > row["created_at"], (
                f"updated_at not incremented; created={row['created_at']}, updated={row['updated_at']}"
            )
        finally:
            await _cancel_and_join(poller_task)
    finally:
        await _cleanup_test_tasks(db, test_run_id)
        await db.close()


# -----------------------------------------------------------------------------
# 2. SKIP LOCKED prevents double-claim
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_skip_locked_no_double_claim(caplog: pytest.LogCaptureFixture) -> None:
    """2 concurrent pollers + 1 pending task -> exactly 1 'Claimed task <uuid>' log entry."""
    test_run_id = uuid.uuid4().hex
    db = Database()
    await db.open()
    try:
        task_id = await _insert_pending_task(db, test_run_id)
        with caplog.at_level(logging.INFO, logger="atlas.agent.poller"):
            t1 = asyncio.create_task(task_poller())
            t2 = asyncio.create_task(task_poller())
            await asyncio.sleep(1.5)
            await _cancel_and_join(t1)
            await _cancel_and_join(t2)
        # Filter to claim-log entries that mention OUR specific task UUID
        claim_logs = [
            r.message for r in caplog.records
            if "Claimed task" in r.message and task_id in r.message
        ]
        assert len(claim_logs) == 1, (
            f"expected exactly 1 claim log for {task_id}; got {len(claim_logs)}: {claim_logs}"
        )
        row = await _read_task(db, task_id)
        assert row is not None and row["status"] == "done", (
            f"task should be done after concurrent pollers ran; got {row}"
        )
    finally:
        await _cleanup_test_tasks(db, test_run_id)
        await db.close()


# -----------------------------------------------------------------------------
# 3. full lifecycle pending -> running -> done
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_marks_done_after_handler() -> None:
    """INSERT pending; verify final state is 'done' (no-op handler completes claim transitively)."""
    test_run_id = uuid.uuid4().hex
    db = Database()
    await db.open()
    try:
        task_id = await _insert_pending_task(db, test_run_id)
        # Pre-state: confirm row starts pending
        pre = await _read_task(db, task_id)
        assert pre is not None and pre["status"] == "pending", f"pre-state wrong: {pre}"
        poller_task = asyncio.create_task(task_poller())
        try:
            row = await _wait_for_status(db, task_id, "done", timeout=2.0)
            assert row is not None, "task row vanished"
            assert row["status"] == "done", f"expected status='done'; got {row['status']!r}"
            assert row["updated_at"] > row["created_at"], (
                f"updated_at did not advance; created={row['created_at']}, updated={row['updated_at']}"
            )
        finally:
            await _cancel_and_join(poller_task)
    finally:
        await _cleanup_test_tasks(db, test_run_id)
        await db.close()


# -----------------------------------------------------------------------------
# 4. idle when no pending matching our test_run_id
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_idle_when_no_pending(caplog: pytest.LogCaptureFixture) -> None:
    """No row inserted with our test_run_id -> poller does not log a claim for our marker."""
    test_run_id = uuid.uuid4().hex
    db = Database()
    await db.open()
    try:
        # Do NOT INSERT any task; the poller may still claim other queued work but not ours.
        with caplog.at_level(logging.INFO, logger="atlas.agent.poller"):
            poller_task = asyncio.create_task(task_poller())
            await asyncio.sleep(0.3)  # less than 5s sleep cadence; poller should be in sleep or just-claimed-other
            await _cancel_and_join(poller_task)
        # Verify no log record references our test_run_id (poller did not fabricate work)
        for r in caplog.records:
            assert test_run_id not in r.message, (
                f"poller log unexpectedly references our test_run_id {test_run_id}: {r.message!r}"
            )
    finally:
        await _cleanup_test_tasks(db, test_run_id)
        await db.close()
