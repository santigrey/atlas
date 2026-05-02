"""Cron-like scheduler. Runs domain work at defined cadences.

Phase 3 Day 78 wiring (Sloan directive):
- system_vitals_check: every 5min
- service_uptime_check: every 5min
- substrate_anchor_check: hourly (3600s)

Phase 4 Day 78 wiring (Talent operations):
- job_search_log_check: daily at-or-after 08:00 UTC, once per UTC date
- weekly_digest_compile: Mondays at-or-after 07:00 America/Denver, once per ISO week

Phase 5 Day 78 wiring (Vendor & admin):
- vendor_renewal_check: daily at-or-after 06:00 UTC, once per UTC date
- tailscale_authkey_check: daily at-or-after 06:00 UTC, once per UTC date
- github_pat_check: daily at-or-after 06:00 UTC, once per UTC date

Phase 6 Day 78 wiring (Mercury supervision):
- mercury_liveness_check: every 5min (capital protection: alert if scanner down)
- mercury_real_money_failclosed: every 5min (continuous gate; Tier 3 immediate)
- mercury_trade_activity_check: daily at-or-after 08:00 UTC, once per UTC date

First tick at scheduler start fires all due cycles immediately (last_run empty -> due);
wall-clock-anchored cadences fire only when the wall-clock window is open.
Each domain call is wrapped in try/except so one failure does not poison the cadence dict
or break the next iteration.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from atlas.db import Database
from atlas.agent.domains.infrastructure import (
    service_uptime_check,
    substrate_anchor_check,
    system_vitals_check,
)
from atlas.agent.domains.talent import (
    job_search_log_check,
    weekly_digest_compile,
)
from atlas.agent.domains.vendor import (
    github_pat_check,
    tailscale_authkey_check,
    vendor_renewal_check,
)
from atlas.agent.domains.mercury import (
    mercury_liveness_check,
    mercury_real_money_failclosed,
    mercury_trade_activity_check,
)

log = logging.getLogger(__name__)

# Interval-based cadences (seconds)
CADENCE_VITALS_S = 300       # 5 minutes
CADENCE_UPTIME_S = 300       # 5 minutes (Sloan directive Day 78; spec said 1min -- 5min ratified)
CADENCE_ANCHOR_S = 3600      # 1 hour
CADENCE_MERCURY_S = 300      # 5 minutes for liveness + real-money fail-closed
TICK_INTERVAL_S = 60         # 1-minute scheduler tick

# Wall-clock-anchored cadences (Phase 4)
TALENT_LOG_HOUR_UTC = 8                     # daily 08:00 UTC
TALENT_DIGEST_WEEKDAY = 0                   # 0=Monday
TALENT_DIGEST_HOUR_LOCAL = 7                # 07:00 local
TALENT_DIGEST_TZ = "America/Denver"         # Denver per Sloan location

# Wall-clock-anchored cadences (Phase 5)
VENDOR_HOUR_UTC = 6                         # daily 06:00 UTC for all 3 vendor checks

# Wall-clock-anchored cadences (Phase 6)
MERCURY_TRADE_HOUR_UTC = 8                  # daily 08:00 UTC for trade activity check


def _daily_utc_due(now_utc: datetime, last_fire: Optional[datetime], target_hour: int) -> bool:
    """True if now_utc is at-or-after target_hour UTC today AND we haven't fired today."""
    if now_utc.hour < target_hour:
        return False
    if last_fire is None:
        return True
    return last_fire.date() != now_utc.date()


def _weekly_local_due(
    now_utc: datetime,
    last_fire: Optional[datetime],
    weekday: int,
    target_hour: int,
    tz_name: str,
) -> bool:
    """True if local-now matches weekday + at-or-after target_hour AND we haven't fired this ISO week."""
    tz = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(tz)
    if local_now.weekday() != weekday or local_now.hour < target_hour:
        return False
    if last_fire is None:
        return True
    last_fire_local = last_fire.astimezone(tz)
    return last_fire_local.isocalendar()[:2] != local_now.isocalendar()[:2]


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

        # Talent log check (daily 08:00 UTC; once per UTC date)
        prev = last_run.get("talent_log")
        if _daily_utc_due(now, prev, TALENT_LOG_HOUR_UTC):
            try:
                await job_search_log_check(db)
            except Exception as e:
                log.exception(f"job_search_log_check failed: {e}")
            last_run["talent_log"] = now

        # Talent weekly digest (Mondays 07:00 America/Denver; once per ISO week)
        prev = last_run.get("talent_digest")
        if _weekly_local_due(
            now, prev, TALENT_DIGEST_WEEKDAY, TALENT_DIGEST_HOUR_LOCAL, TALENT_DIGEST_TZ
        ):
            try:
                await weekly_digest_compile(db)
            except Exception as e:
                log.exception(f"weekly_digest_compile failed: {e}")
            last_run["talent_digest"] = now

        # Vendor renewal check (daily 06:00 UTC; once per UTC date)
        prev = last_run.get("vendor_renewal")
        if _daily_utc_due(now, prev, VENDOR_HOUR_UTC):
            try:
                await vendor_renewal_check(db)
            except Exception as e:
                log.exception(f"vendor_renewal_check failed: {e}")
            last_run["vendor_renewal"] = now

        # Tailscale auth key expiry check (daily 06:00 UTC; once per UTC date)
        prev = last_run.get("tailscale_authkey")
        if _daily_utc_due(now, prev, VENDOR_HOUR_UTC):
            try:
                await tailscale_authkey_check(db)
            except Exception as e:
                log.exception(f"tailscale_authkey_check failed: {e}")
            last_run["tailscale_authkey"] = now

        # GitHub PAT expiry check (daily 06:00 UTC; once per UTC date)
        prev = last_run.get("github_pat")
        if _daily_utc_due(now, prev, VENDOR_HOUR_UTC):
            try:
                await github_pat_check(db)
            except Exception as e:
                log.exception(f"github_pat_check failed: {e}")
            last_run["github_pat"] = now

        # Mercury liveness check (5min cadence; capital protection)
        prev = last_run.get("mercury_liveness")
        if prev is None or (now - prev).total_seconds() >= CADENCE_MERCURY_S:
            try:
                await mercury_liveness_check(db)
            except Exception as e:
                log.exception(f"mercury_liveness_check failed: {e}")
            last_run["mercury_liveness"] = now

        # Mercury real-money fail-closed (5min cadence; continuous gate)
        prev = last_run.get("mercury_real_money")
        if prev is None or (now - prev).total_seconds() >= CADENCE_MERCURY_S:
            try:
                await mercury_real_money_failclosed(db)
            except Exception as e:
                log.exception(f"mercury_real_money_failclosed failed: {e}")
            last_run["mercury_real_money"] = now

        # Mercury trade activity (daily 08:00 UTC; once per UTC date)
        prev = last_run.get("mercury_trade_activity")
        if _daily_utc_due(now, prev, MERCURY_TRADE_HOUR_UTC):
            try:
                await mercury_trade_activity_check(db)
            except Exception as e:
                log.exception(f"mercury_trade_activity_check failed: {e}")
            last_run["mercury_trade_activity"] = now

        await asyncio.sleep(TICK_INTERVAL_S)
