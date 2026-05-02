"""Cron-like scheduler. Runs domain work at defined cadences.

Phase 3 Day 78 wiring (Sloan directive):
- system_vitals_check: every 5min
- service_uptime_check: every 5min
- substrate_anchor_check: hourly (3600s)

First tick at scheduler start fires all due cycles immediately (last_run empty -> due).
Each domain call is wrapped in try/except so one failure does not poison the cadence dict
or break the next iteration.
"""
import asyncio
import logging
from datetime import datetime, timezone

from atlas.db import Database
from atlas.agent.domains.infrastructure import (
    service_uptime_check,
    substrate_anchor_check,
    system_vitals_check,
)

log = logging.getLogger(__name__)

# Cadence in seconds
CADENCE_VITALS_S = 300       # 5 minutes
CADENCE_UPTIME_S = 300       # 5 minutes (Sloan directive Day 78; spec said 1min -- 5min ratified)
CADENCE_ANCHOR_S = 3600      # 1 hour
TICK_INTERVAL_S = 60         # 1-minute scheduler tick


async def scheduler():
    """Tick once per minute; dispatch domain work based on cadence rules."""
    db = Database()
    last_run: dict[str, datetime] = {}  # domain_name -> last UTC datetime
    while True:
        now = datetime.now(tz=timezone.utc)

        # Vitals (5min cadence)
        prev = last_run.get("vitals")
        if prev is None or (now - prev).total_seconds() >= CADENCE_VITALS_S:
            try:
                await system_vitals_check(db)
            except Exception as e:
                log.exception(f"system_vitals_check failed: {e}")
            last_run["vitals"] = now

        # Service uptime (5min cadence)
        prev = last_run.get("uptime")
        if prev is None or (now - prev).total_seconds() >= CADENCE_UPTIME_S:
            try:
                await service_uptime_check(db)
            except Exception as e:
                log.exception(f"service_uptime_check failed: {e}")
            last_run["uptime"] = now

        # Substrate anchor (hourly cadence)
        prev = last_run.get("anchor")
        if prev is None or (now - prev).total_seconds() >= CADENCE_ANCHOR_S:
            try:
                await substrate_anchor_check(db)
            except Exception as e:
                log.exception(f"substrate_anchor_check failed: {e}")
            last_run["anchor"] = now

        await asyncio.sleep(TICK_INTERVAL_S)
