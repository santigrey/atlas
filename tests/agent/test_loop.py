"""Atlas v0.1 Phase 8 -- Unit tests for atlas.agent.loop crash isolation.

3 cases per paco_directive_atlas_v0_1_phase8.md section 2.4:
1. test_loop_runs_three_coroutines -- all 3 sentinels spawn and run at least once
2. test_loop_crash_isolation -- one coroutine raising does NOT stop the other 2
3. test_loop_logs_crashes -- caplog captures '<name> crashed: <err>' on synthetic crash

All 3 monkey-patch atlas.agent.loop.{task_poller,scheduler,event_subscriber} + collapse
the 30s restart sleep to ~0s for fast runs. No DB / network / homelab dependency;
tests are pure-mock and run in CI mode.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from atlas.agent import loop

# Capture pre-patch asyncio.sleep so tests can use real timing for setup/teardown
_REAL_SLEEP = asyncio.sleep


async def _fast_sleep(delay: float) -> None:
    """Collapse the 30s restart delay to 0s; preserve normal short sleeps."""
    if delay >= 30:
        await _REAL_SLEEP(0)
    else:
        await _REAL_SLEEP(delay)


async def _run_loop_briefly(duration: float = 0.2) -> None:
    """Run loop.run() in a task; cancel after `duration` seconds."""
    task = asyncio.create_task(loop.run())
    await _REAL_SLEEP(duration)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# -----------------------------------------------------------------------------
# 1. all 3 coroutines spawn and run at least once
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_runs_three_coroutines(monkeypatch: pytest.MonkeyPatch) -> None:
    """task_poller, scheduler, event_subscriber each invoked >=1 time."""
    counters = {"poller": 0, "scheduler": 0, "subscriber": 0}

    async def fake_poller() -> None:
        counters["poller"] += 1
        await _REAL_SLEEP(0.02)

    async def fake_scheduler() -> None:
        counters["scheduler"] += 1
        await _REAL_SLEEP(0.02)

    async def fake_subscriber() -> None:
        counters["subscriber"] += 1
        await _REAL_SLEEP(0.02)

    monkeypatch.setattr(loop, "task_poller", fake_poller)
    monkeypatch.setattr(loop, "scheduler", fake_scheduler)
    monkeypatch.setattr(loop, "event_subscriber", fake_subscriber)
    monkeypatch.setattr(loop.asyncio, "sleep", _fast_sleep)

    await _run_loop_briefly(0.15)

    assert counters["poller"] >= 1, f"task_poller not called; got {counters}"
    assert counters["scheduler"] >= 1, f"scheduler not called; got {counters}"
    assert counters["subscriber"] >= 1, f"event_subscriber not called; got {counters}"


# -----------------------------------------------------------------------------
# 2. crash isolation -- one coroutine raising does not stop the others
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_crash_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """task_poller raises immediately; scheduler + event_subscriber continue running."""
    counters = {"poller": 0, "scheduler": 0, "subscriber": 0}

    async def crashing_poller() -> None:
        counters["poller"] += 1
        raise RuntimeError("synthetic crash for crash_isolation test")

    async def fake_scheduler() -> None:
        counters["scheduler"] += 1
        await _REAL_SLEEP(0.02)

    async def fake_subscriber() -> None:
        counters["subscriber"] += 1
        await _REAL_SLEEP(0.02)

    monkeypatch.setattr(loop, "task_poller", crashing_poller)
    monkeypatch.setattr(loop, "scheduler", fake_scheduler)
    monkeypatch.setattr(loop, "event_subscriber", fake_subscriber)
    monkeypatch.setattr(loop.asyncio, "sleep", _fast_sleep)

    await _run_loop_briefly(0.2)

    # Crashing poller restarts -- proven by >=2 invocations through the isolate retry loop
    assert counters["poller"] >= 2, f"crashing poller did not restart; got {counters}"
    # Scheduler + subscriber continue running independently of the crash
    assert counters["scheduler"] >= 2, f"scheduler stopped after poller crash; got {counters}"
    assert counters["subscriber"] >= 2, f"subscriber stopped after poller crash; got {counters}"


# -----------------------------------------------------------------------------
# 3. crash log line captured via log.exception
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_logs_crashes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """log.exception in isolate() emits '<name> crashed: <err>' on coroutine raise."""
    crash_token = "synthetic_crash_token_abc123"

    async def crashing_poller() -> None:
        raise RuntimeError(crash_token)

    async def benign() -> None:
        await _REAL_SLEEP(0.02)

    monkeypatch.setattr(loop, "task_poller", crashing_poller)
    monkeypatch.setattr(loop, "scheduler", benign)
    monkeypatch.setattr(loop, "event_subscriber", benign)
    monkeypatch.setattr(loop.asyncio, "sleep", _fast_sleep)

    with caplog.at_level(logging.ERROR, logger="atlas.agent.loop"):
        await _run_loop_briefly(0.1)

    messages = [r.message for r in caplog.records]
    assert any(
        "task_poller crashed" in m and crash_token in m for m in messages
    ), f"expected 'task_poller crashed: {crash_token}' in caplog; got: {messages}"
