"""Atlas v0.1 Phase 8 -- Integration test for full agent loop end-to-end.

1 case per paco_directive_atlas_v0_1_phase8.md section 2.10 (ADAPTED to ground-truth
surface per Path B; Sloan Day 78 evening):

- test_full_agent_loop_lifecycle_and_scheduler -- 1 test marker row + spawned loop;
  verify lifecycle (pending->done) AND scheduler wrote >=1 domain row in test window.

ADAPTATION RATIONALE (preserves directive intent; matches actual implementation):
- Domains 1-4 write to atlas.tasks via _create_monitoring_task (per Sloan directive
  Day 78 morning + Phase 3 close substrate-gap amendment), NOT to atlas.events.
  Only Phase 7 mercury_control writes to atlas.events.
- task_poller has no-op handler in v0.1; pending rows transition pending->done
  regardless of payload.kind (no domain dispatch). One test row suffices for lifecycle.
- event_subscriber is a v0.1 placeholder (sleep+heartbeat); writes nothing; not verified.
- Scheduler first tick fires immediately on startup (last_run dict empty); 5min-cadence
  domain checks (vitals/uptime/anchor/mercury_liveness/mercury_real_money) all execute
  in first tick. First tick wallclock ~28s typical (parallel SSH probes via gather).
- Cleanup: ONLY rows tagged with payload.test_run_id (our marker). Domain-side rows are
  legitimate observation data and stay in atlas.tasks (real production state).

Triple-marked @pytest.mark.integration @pytest.mark.slow @pytest.mark.homelab so it
is skipped in fast CI (pytest -m 'not homelab and not slow and not integration').
Run manually on Beast: `pytest tests/integration/ -v` or via tagged CI run.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from atlas.agent import loop
from atlas.db import Database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.homelab,
]

# Domain kinds that scheduler's first 5min-cadence tick produces.
# Wall-clock-anchored checks (talent_log/vendor/mercury_trade) only fire at certain
# UTC hours so we cannot rely on them within a 45s test window.
_FIRST_TICK_DOMAIN_KINDS = (
    "monitoring_cpu",
    "monitoring_ram",
    "monitoring_disk",
    "service_uptime",
    "substrate_check",
    "mercury_liveness_warning",            # only if mercury inactive (not always written)
    "mercury_failclosed_check_error",      # only if cross-host query fails
    "mercury_real_money_unauthorized",     # only if doc absent + real_count > 0
)

# Reliable evidence: vitals (15 rows) + uptime (6 rows) + substrate (1 row) ALWAYS write
# regardless of probe success/failure. We assert >=1 row from this reliable subset.
_RELIABLE_FIRST_TICK_KINDS = (
    "monitoring_cpu",
    "monitoring_ram",
    "monitoring_disk",
    "service_uptime",
    "substrate_check",
)

# Wall-clock observation: first scheduler tick + cleanup completes well under 60s in
# typical homelab conditions. 45s wait gives ~15s margin over the ~28s typical tick.
_LOOP_RUN_SECONDS = 45


async def _insert_test_marker_row(db: Database, test_run_id: str) -> str:
    payload = {
        "test_run_id": test_run_id,
        "kind": "test_integration_marker",
        "note": "Phase 8 integration test marker; cleanup deletes by test_run_id",
    }
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


async def _read_task_status(db: Database, task_id: str) -> str | None:
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status FROM atlas.tasks WHERE id = %s", (task_id,)
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def _count_domain_rows_since(
    db: Database, since: datetime, kinds: tuple[str, ...]
) -> dict[str, int]:
    """Return per-kind counts of atlas.tasks rows created since `since` matching `kinds`."""
    counts: dict[str, int] = {}
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT payload->>'kind' AS k, count(*) FROM atlas.tasks "
                "WHERE created_at >= %s "
                "AND payload->>'kind' = ANY(%s) "
                "GROUP BY payload->>'kind'",
                (since, list(kinds)),
            )
            rows = await cur.fetchall()
            for r in rows:
                counts[r[0]] = int(r[1])
    return counts


async def _cleanup_test_rows(db: Database, test_run_id: str) -> None:
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM atlas.tasks WHERE payload->>'test_run_id' = %s",
                (test_run_id,),
            )
            await conn.commit()


async def _cancel_and_join(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_full_agent_loop_lifecycle_and_scheduler() -> None:
    """End-to-end: pre-INSERT 1 marker row; spawn loop; verify lifecycle + scheduler ran.

    Acceptance:
      A. Test marker row transitions pending -> done within the loop run window (proves
         task_poller claims + processes pending atlas.tasks rows end-to-end).
      B. >=1 atlas.tasks row was created during the run window with payload.kind in the
         reliable scheduler-domain set (proves scheduler dispatched at least one domain
         check that landed a row, even with partial probe failures).
    """
    test_run_id = uuid.uuid4().hex
    db = Database()
    await db.open()
    test_window_start = datetime.now(tz=timezone.utc)
    loop_task: asyncio.Task[Any] | None = None
    try:
        # Pre-INSERT the marker row
        marker_task_id = await _insert_test_marker_row(db, test_run_id)
        # Spawn loop in background
        loop_task = asyncio.create_task(loop.run())
        # Wait LOOP_RUN_SECONDS while the loop processes
        wallclock_start = time.monotonic()
        await asyncio.sleep(_LOOP_RUN_SECONDS)
        wallclock_elapsed = time.monotonic() - wallclock_start

        # Verify A: marker row reached 'done'
        final_status = await _read_task_status(db, marker_task_id)
        assert final_status == "done", (
            f"marker row did not reach 'done' state in {_LOOP_RUN_SECONDS}s window; "
            f"final status={final_status!r}; wallclock_elapsed={wallclock_elapsed:.1f}s"
        )

        # Verify B: scheduler wrote >=1 reliable-domain row during the test window
        counts = await _count_domain_rows_since(
            db, test_window_start, _RELIABLE_FIRST_TICK_KINDS
        )
        total_reliable = sum(counts.values())
        assert total_reliable >= 1, (
            f"no reliable scheduler-domain rows written during test window; "
            f"expected >=1 (vitals or uptime or substrate); got counts={counts}; "
            f"wallclock_elapsed={wallclock_elapsed:.1f}s"
        )

        # Diagnostic counters (informational; not asserted strictly)
        all_kinds_counts = await _count_domain_rows_since(
            db, test_window_start, _FIRST_TICK_DOMAIN_KINDS
        )
        print(
            f"\nintegration_test_diagnostics: "
            f"reliable_total={total_reliable} "
            f"all_first_tick_counts={all_kinds_counts} "
            f"wallclock_elapsed={wallclock_elapsed:.1f}s"
        )
    finally:
        if loop_task is not None:
            await _cancel_and_join(loop_task)
        # Cleanup: only rows tagged with our test_run_id (domain rows preserved as legitimate observations)
        await _cleanup_test_rows(db, test_run_id)
        await db.close()
