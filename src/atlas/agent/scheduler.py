"""Cron-like scheduler. Runs domain work at defined cadences."""
import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

async def scheduler():
    """Tick once per minute; dispatch domain work based on cadence rules."""
    last_run = {}  # domain_name -> last UTC datetime
    while True:
        now = datetime.now(tz=timezone.utc)
        # Domain dispatchers added Phase 3+
        await asyncio.sleep(60)
