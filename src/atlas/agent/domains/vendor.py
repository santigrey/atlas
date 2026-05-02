"""Atlas Operations Agent -- Domain 3: Vendor & admin.

Three cadenced checks (daily 06:00 UTC per spec lines 342-393 + Atlas SOP v1.0 Section 3.1):
- vendor_renewal_check: query atlas.vendors WHERE status='active' AND renewal_date IS NOT NULL;
  flag 14-day (Tier 2 / severity='warn') and 3-day (Tier 3 / severity='critical') windows.
  Past-due renewals also raise critical (vendor data needs Sloan attention).
- tailscale_authkey_check: parse `tailscale status --json` for Self.KeyExpiry;
  alert if expiry < 30 days (Tier 2 / severity='warn').
- github_pat_check: parse atlas.vendors.notes for `pat_expires_at:YYYY-MM-DD` (vendor 'GitHub');
  alert if expiry < 30 days (Tier 2 / severity='warn'). v0.1.1 will wire real GitHub API.

Per amended spec (Phase 3 close substrate-gap preamble): all writes go to atlas.tasks via
_create_monitoring_task. v0.1.1 will migrate Domain 1-4 writes to atlas.events when canonical
create_event helper lands alongside Mr Robot build.

P6 #32 reuse pattern: _create_monitoring_task + _local_run imported directly from
infrastructure.py rather than reauthored.

Per-day dedup (UTC): avoids duplicate warning rows on agent restart same UTC day.
Each emit gated by an existence check on (kind, vendor_name, severity, today UTC) tuple.

All probes READ-ONLY: SELECTs against atlas.vendors; tailscale CLI status read; never mutate.

Tier mapping (per Atlas SOP v1.0 Section 3.2):
- Tier 2 / severity='warn'     = 14-day renewal warning, 30-day Tailscale, 30-day GitHub PAT
- Tier 3 / severity='critical' = 3-day renewal warning, past-due renewal
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from atlas.db import Database
from atlas.agent.domains.infrastructure import _create_monitoring_task, _local_run

log = logging.getLogger(__name__)

# Renewal warning thresholds (days)
RENEWAL_TIER_2_DAYS = 14
RENEWAL_TIER_3_DAYS = 3
# Tailscale + GitHub PAT thresholds
TAILSCALE_THRESHOLD_DAYS = 30
GITHUB_PAT_THRESHOLD_DAYS = 30

# v0.1: GitHub PAT expiration is tracked manually in atlas.vendors.notes.
# Format: "pat_expires_at:YYYY-MM-DD" (anywhere in notes string, case-insensitive marker).
# v0.1.1 will wire the real GitHub API to read PAT expiration directly.
GITHUB_PAT_NOTE_MARKER = "pat_expires_at:"
GITHUB_PAT_DATE_LEN = 10  # YYYY-MM-DD


async def _alert_already_today(
    db: Database,
    kind: str,
    vendor_name: Optional[str] = None,
    severity: Optional[str] = None,
) -> bool:
    """Per-day dedup: True if a task with matching keys was created today UTC.

    Prevents duplicate warnings if the agent restarts and re-fires the same daily check.
    Today is computed in Python as UTC midnight to avoid PG session-tz ambiguity.
    Fails open: dedup-query failure allows the write (better to spam than miss).
    """
    today_start = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    where = ["payload->>'kind' = %s", "created_at >= %s"]
    args: list[Any] = [kind, today_start]
    if vendor_name is not None:
        where.append("payload->>'vendor_name' = %s")
        args.append(vendor_name)
    if severity is not None:
        where.append("payload->>'severity' = %s")
        args.append(severity)
    sql = f"SELECT 1 FROM atlas.tasks WHERE {' AND '.join(where)} LIMIT 1"
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                row = await cur.fetchone()
                return row is not None
    except Exception as e:
        log.exception(f"_alert_already_today query failed kind={kind}: {e}")
        return False


async def vendor_renewal_check(db: Database) -> None:
    """Daily 06:00 UTC: scan atlas.vendors for upcoming renewals; flag 14-day + 3-day.

    Tier 3 takes precedence over Tier 2: if a vendor is within 3 days, only the
    critical alert is written (not also a warn alert). One row per vendor per severity
    per UTC day. Past-due renewals raise critical (vendor data stale).
    """
    log.info("vendor_renewal_check_start")
    today = datetime.now(tz=timezone.utc).date()
    rows: list[tuple[str, date, Any]] = []
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT name, renewal_date, monthly_cost_usd FROM atlas.vendors "
                    "WHERE status = 'active' AND renewal_date IS NOT NULL "
                    "ORDER BY renewal_date ASC"
                )
                rows = await cur.fetchall()
    except Exception as e:
        log.exception(f"vendor_renewal_check query failed: {e}")
        return

    written = 0
    for name, renewal_date, monthly_cost in rows:
        days_until = (renewal_date - today).days
        if days_until < 0:
            severity, threshold = "critical", "past_due"
        elif days_until <= RENEWAL_TIER_3_DAYS:
            severity, threshold = "critical", "3_day"
        elif days_until <= RENEWAL_TIER_2_DAYS:
            severity, threshold = "warn", "14_day"
        else:
            continue  # Outside both windows; no alert

        if await _alert_already_today(
            db, "vendor_renewal_warning", vendor_name=name, severity=severity
        ):
            log.debug(f"vendor_renewal_check skip dup name={name} severity={severity}")
            continue

        task_id = await _create_monitoring_task(
            db,
            "vendor_renewal_warning",
            {
                "vendor_name": name,
                "renewal_date": renewal_date.isoformat(),
                "days_until": days_until,
                "monthly_cost_usd": float(monthly_cost) if monthly_cost is not None else None,
                "severity": severity,
                "threshold": threshold,
            },
        )
        if task_id:
            written += 1
    log.info(f"vendor_renewal_check_done scanned={len(rows)} written={written}")


async def tailscale_authkey_check(db: Database) -> None:
    """Daily 06:00 UTC: parse `tailscale status --json` Self.KeyExpiry; alert if <30 days.

    Atlas runs on Beast where tailscale is installed and authenticated as user jes.
    Uses _local_run (NOT _ssh_run) because the CLI is on the same host.
    severity='warn' (Tier 2) when expiry within 30 days; 'critical' if past due.
    """
    log.info("tailscale_authkey_check_start")
    rc, stdout, stderr = await _local_run("tailscale status --json", timeout=10.0)
    if rc != 0:
        log.warning(
            f"tailscale_authkey_check skipped: tailscale CLI failed rc={rc} stderr={stderr[:200]}"
        )
        return
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        log.warning(f"tailscale_authkey_check JSON parse failed: {e}")
        return

    self_block = data.get("Self") or {}
    key_expiry_str = self_block.get("KeyExpiry")
    if not key_expiry_str:
        log.info("tailscale_authkey_check no KeyExpiry field; tailnet may not require key rotation")
        return

    try:
        # KeyExpiry format: "2026-10-28T05:56:04Z" (ISO 8601 with Z suffix)
        expiry_dt = datetime.fromisoformat(key_expiry_str.replace("Z", "+00:00"))
    except ValueError as e:
        log.warning(f"tailscale_authkey_check expiry parse failed: {e}; raw={key_expiry_str!r}")
        return

    now = datetime.now(tz=timezone.utc)
    days_until = (expiry_dt - now).days

    if days_until > TAILSCALE_THRESHOLD_DAYS:
        log.info(
            f"tailscale_authkey_check_done days_until={days_until} (above threshold; no alert)"
        )
        return

    severity = "warn" if days_until > 0 else "critical"
    if await _alert_already_today(db, "tailscale_authkey_warning", severity=severity):
        log.debug("tailscale_authkey_check skip dup")
        return

    task_id = await _create_monitoring_task(
        db,
        "tailscale_authkey_warning",
        {
            "host": self_block.get("HostName"),
            "node_id": self_block.get("ID"),
            "key_expiry": key_expiry_str,
            "days_until": days_until,
            "severity": severity,
        },
    )
    log.info(f"tailscale_authkey_check_done days_until={days_until} task_id={task_id}")


async def github_pat_check(db: Database) -> None:
    """Daily 06:00 UTC: parse atlas.vendors.notes for GitHub PAT expiration; alert if <30 days.

    v0.1: manual tracking via notes column (`pat_expires_at:YYYY-MM-DD` anywhere in notes).
    v0.1.1: wire real GitHub API to read PAT expiration directly.
    severity='warn' (Tier 2) when expiry within 30 days; 'critical' if past due.
    """
    log.info("github_pat_check_start")
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT notes FROM atlas.vendors "
                    "WHERE name = 'GitHub' AND status = 'active'"
                )
                row = await cur.fetchone()
    except Exception as e:
        log.exception(f"github_pat_check vendor query failed: {e}")
        return

    if not row or not row[0]:
        log.info("github_pat_check no GitHub vendor notes; manual-track expected (v0.1.1 wires API)")
        return

    notes: str = row[0]
    idx = notes.lower().find(GITHUB_PAT_NOTE_MARKER)
    if idx < 0:
        log.info("github_pat_check no pat_expires_at marker in notes")
        return

    date_str = notes[idx + len(GITHUB_PAT_NOTE_MARKER):idx + len(GITHUB_PAT_NOTE_MARKER) + GITHUB_PAT_DATE_LEN]
    try:
        expiry = date.fromisoformat(date_str)
    except ValueError as e:
        log.warning(f"github_pat_check date parse failed: {e}; raw={date_str!r}")
        return

    today = datetime.now(tz=timezone.utc).date()
    days_until = (expiry - today).days

    if days_until > GITHUB_PAT_THRESHOLD_DAYS:
        log.info(
            f"github_pat_check_done days_until={days_until} (above threshold; no alert)"
        )
        return

    severity = "warn" if days_until > 0 else "critical"
    if await _alert_already_today(db, "github_pat_warning", severity=severity):
        log.debug("github_pat_check skip dup")
        return

    task_id = await _create_monitoring_task(
        db,
        "github_pat_warning",
        {
            "expiry_date": expiry.isoformat(),
            "days_until": days_until,
            "severity": severity,
            "source": "atlas.vendors.notes (manual track v0.1)",
        },
    )
    log.info(f"github_pat_check_done days_until={days_until} task_id={task_id}")
