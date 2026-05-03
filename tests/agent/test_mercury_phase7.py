"""Atlas v0.1 Phase 7.2 -- Integration tests for mercury_start/mercury_stop cancel-window.

3 cases per paco_directive_atlas_v0_1_phase7.md section 2.5:
1. test_mercury_start_no_cancel_invokes_systemctl -- window elapses; ssh_run called with 'systemctl start'
2. test_mercury_start_with_cancel_aborts -- cancel claim inserted mid-window; ssh_run NOT called
3. test_mercury_stop_outcome_systemctl_error -- ssh_run rc=1; outcome='systemctl_error'

All tests monkey-patch _CANCEL_WINDOW_S to a small value (2-3s) for runtime sanity.
_ssh_run is monkey-patched so NO real SSH calls hit CK during tests.
emit_event calls are captured via real DB write + readback (atlas.events rows; cleanup in finally).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from atlas.agent.domains import mercury
from atlas.agent.domains.mercury import mercury_start, mercury_stop
from atlas.db import Database


# Test cancel-window: 3s gives 3 polling iterations (sleep 1s each)
TEST_CANCEL_WINDOW_S = 3


async def _cleanup_test_events(db: Database, source_prefix: str) -> None:
    """Delete atlas.events rows written during a test."""
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM atlas.events WHERE source = %s",
                (source_prefix,),
            )
            await conn.commit()


async def _cleanup_test_tasks(db: Database, task_id: Any) -> None:
    """Delete atlas.tasks row by id."""
    if task_id is None:
        return
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM atlas.tasks WHERE id = %s", (task_id,))
            await conn.commit()


async def _read_events_by_source(db: Database, source: str) -> list[dict[str, Any]]:
    """Return list of {kind, payload} dicts for atlas.events rows from source, oldest first."""
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT kind, payload FROM atlas.events WHERE source = %s ORDER BY id",
                (source,),
            )
            rows = await cur.fetchall()
            return [{"kind": r[0], "payload": r[1]} for r in rows]


# -----------------------------------------------------------------------------
# 1. start with no cancel -> ssh_run called with 'systemctl start'
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mercury_start_no_cancel_invokes_systemctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Window elapses without cancel; _ssh_run called once with 'systemctl start mercury-scanner.service';
    2 emit_event calls land in atlas.events (initiated warn + executed info)."""
    # Shrink cancel-window for test speed (3s -> 3 iterations @ 1s each)
    monkeypatch.setattr(mercury, "_CANCEL_WINDOW_S", TEST_CANCEL_WINDOW_S)
    # Mock _ssh_run to return success without touching CK
    mock_ssh = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(mercury, "_ssh_run", mock_ssh)

    db = Database()
    await db.open()
    try:
        await mercury_start(db)
        # Verify _ssh_run called exactly once with action=start
        assert mock_ssh.call_count == 1, f"expected 1 _ssh_run call; got {mock_ssh.call_count}"
        ssh_args = mock_ssh.call_args.args
        assert ssh_args[0] == mercury.CK_HOST, f"wrong host: {ssh_args[0]!r}"
        assert ssh_args[1] == mercury.CK_USER, f"wrong user: {ssh_args[1]!r}"
        ssh_cmd = ssh_args[2]
        assert "sudo systemctl start" in ssh_cmd, f"cmd missing 'systemctl start': {ssh_cmd!r}"
        assert mercury.MERCURY_SERVICE in ssh_cmd, f"cmd missing service name: {ssh_cmd!r}"
        # Verify 2 emit_event rows (initiated warn + executed info)
        events = await _read_events_by_source(db, "atlas.mercury")
        kinds = [e["kind"] for e in events]
        assert "mercury_control_initiated" in kinds, f"missing initiated; got {kinds}"
        assert "mercury_control_executed" in kinds, f"missing executed; got {kinds}"
        # Initiated event has severity=warn, tier=2
        initiated = next(e for e in events if e["kind"] == "mercury_control_initiated")
        assert initiated["payload"]["severity"] == "warn"
        assert initiated["payload"]["tier"] == 2
        assert initiated["payload"]["action"] == "start"
        # Executed event has severity=info, tier=1, outcome=executed
        executed = next(e for e in events if e["kind"] == "mercury_control_executed")
        assert executed["payload"]["severity"] == "info"
        assert executed["payload"]["tier"] == 1
        assert executed["payload"]["outcome"] == "executed"
        assert executed["payload"]["action"] == "start"
        assert executed["payload"]["rc"] == 0
    finally:
        await _cleanup_test_events(db, "atlas.mercury")
        await db.close()


# -----------------------------------------------------------------------------
# 2. cancel claim mid-window -> ssh_run NOT called; cancel task consumed
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mercury_start_with_cancel_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Insert cancel claim ~1s into the 3s window; verify _ssh_run NOT called,
    cancelled emit_event logged, cancel task status updated to 'done'."""
    monkeypatch.setattr(mercury, "_CANCEL_WINDOW_S", TEST_CANCEL_WINDOW_S)
    mock_ssh = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(mercury, "_ssh_run", mock_ssh)

    db = Database()
    await db.open()
    cancel_task_id: Any = None
    try:
        # Background coroutine: insert cancel claim 1s after mercury_start begins
        async def _insert_cancel_claim() -> Any:
            nonlocal cancel_task_id
            await asyncio.sleep(1.0)
            async with db.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO atlas.tasks (status, payload) "
                        "VALUES ('pending', %s::jsonb) RETURNING id",
                        (json.dumps({"kind": "mercury_control_cancel"}),),
                    )
                    row = await cur.fetchone()
                    cancel_task_id = row[0]
                    await conn.commit()
            return cancel_task_id

        # Run both concurrently
        await asyncio.gather(mercury_start(db), _insert_cancel_claim())

        # Verify _ssh_run NOT called (cancellation aborted the execution)
        assert mock_ssh.call_count == 0, (
            f"_ssh_run should NOT have been called after cancel; got {mock_ssh.call_count} calls"
        )
        # Verify cancelled event present
        events = await _read_events_by_source(db, "atlas.mercury")
        kinds = [e["kind"] for e in events]
        assert "mercury_control_initiated" in kinds
        assert "mercury_control_cancelled" in kinds, f"missing cancelled event; got {kinds}"
        assert "mercury_control_executed" not in kinds, (
            f"executed should NOT fire on cancel; got {kinds}"
        )
        cancelled = next(e for e in events if e["kind"] == "mercury_control_cancelled")
        assert cancelled["payload"]["severity"] == "info"
        assert cancelled["payload"]["tier"] == 1
        assert cancelled["payload"]["action"] == "start"
        assert cancelled["payload"]["cancel_task_id"] == str(cancel_task_id)
        # Verify cancel task consumed (status='done')
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT status FROM atlas.tasks WHERE id = %s", (cancel_task_id,)
                )
                row = await cur.fetchone()
                assert row is not None, "cancel task row missing"
                assert row[0] == "done", f"cancel task should be 'done'; got {row[0]!r}"
    finally:
        await _cleanup_test_events(db, "atlas.mercury")
        await _cleanup_test_tasks(db, cancel_task_id)
        await db.close()


# -----------------------------------------------------------------------------
# 3. ssh_run rc=1 (systemctl error) -> outcome='systemctl_error'
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mercury_stop_outcome_systemctl_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ssh_run returns rc=1 (systemctl error); executed payload.outcome='systemctl_error'."""
    monkeypatch.setattr(mercury, "_CANCEL_WINDOW_S", TEST_CANCEL_WINDOW_S)
    mock_ssh = AsyncMock(return_value=(1, "", "Unit not found"))
    monkeypatch.setattr(mercury, "_ssh_run", mock_ssh)

    db = Database()
    await db.open()
    try:
        await mercury_stop(db)
        # Verify _ssh_run called once with 'systemctl stop'
        assert mock_ssh.call_count == 1
        ssh_cmd = mock_ssh.call_args.args[2]
        assert "sudo systemctl stop" in ssh_cmd
        # Verify executed event with outcome='systemctl_error'
        events = await _read_events_by_source(db, "atlas.mercury")
        kinds = [e["kind"] for e in events]
        assert "mercury_control_executed" in kinds, f"missing executed; got {kinds}"
        executed = next(e for e in events if e["kind"] == "mercury_control_executed")
        assert executed["payload"]["outcome"] == "systemctl_error", (
            f"expected outcome='systemctl_error' on rc=1; got {executed['payload']['outcome']!r}"
        )
        assert executed["payload"]["rc"] == 1
        assert executed["payload"]["action"] == "stop"
        assert "Unit not found" in executed["payload"]["stderr"]
    finally:
        await _cleanup_test_events(db, "atlas.mercury")
        await db.close()
