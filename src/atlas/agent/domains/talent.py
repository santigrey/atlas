"""Atlas Operations Agent -- Domain 2: Talent operations.

Responsibilities (per build spec lines 314-340 + Phase 3 close amendment + handoff Phase 4 GO):
- job_search_log_check(): daily 08:00 UTC; SSH to CK, read job_search_log.json, detect new URLs.
- weekly_digest_compile(): Mondays 07:00 local; aggregate last-7-day applicant_logged rows.
- recruiter_watch(): STUB at v0.1; deferred to v0.1.1 pending Gmail integration.

Writes persist to atlas.tasks via canonical _create_monitoring_task helper (Phase 3 commit
54e3a26). Per amended spec + P6 #33 substrate-gap rationale: atlas.events MCP write helper
deferred to v0.2/Mr Robot per P5 #42; v0.1.1 will migrate Domain 2 writes to atlas.events.

P6 #32 mitigation: pattern reused via direct import of infrastructure._create_monitoring_task
and infrastructure._ssh_run; not authored fresh from memory.

Cross-host file read: SSH BatchMode to CK (192.168.1.10) using Phase-0-deployed id_ed25519.
No new dependencies. All probes READ-ONLY: cat the file on CK, never write to CK.

Payload kinds emitted:
- 'applicant_logged': one row per untracked URL from job_search_log.json seen_urls
- 'weekly_digest_talent': one row per weekly aggregation pass
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from atlas.db import Database
from atlas.agent.domains.infrastructure import _create_monitoring_task, _ssh_run

log = logging.getLogger(__name__)

# CK is the canonical host for control-plane artifacts (job_search_log.json lives here).
CK_HOST = "192.168.1.10"
CK_USER = "jes"
JOB_SEARCH_LOG_PATH = "/home/jes/control-plane/job_search_log.json"


async def _read_job_search_log() -> Optional[dict[str, Any]]:
    """SSH to CK, cat the job_search_log.json, parse JSON.

    Returns parsed dict on success, None on read/parse failure.
    Read-only: never modifies the file on CK.
    """
    rc, stdout, stderr = await _ssh_run(
        CK_HOST, CK_USER, f"cat {JOB_SEARCH_LOG_PATH}", timeout=10.0
    )
    if rc != 0:
        log.warning(f"job_search_log read failed rc={rc} stderr={stderr[:200]}")
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        log.warning(f"job_search_log JSON parse failed: {e}; stdout={stdout[:200]}")
        return None


async def _existing_logged_urls(db: Database) -> set[str]:
    """Return set of URLs already tracked via atlas.tasks payload.kind='applicant_logged'.

    Status-agnostic: pending/running/done all count as "already logged" so we don't
    re-create rows for URLs the poller has consumed.
    """
    urls: set[str] = set()
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT payload->>'url' FROM atlas.tasks "
                    "WHERE payload->>'kind' = 'applicant_logged' "
                    "AND payload->>'url' IS NOT NULL"
                )
                rows = await cur.fetchall()
                for row in rows:
                    if row[0]:
                        urls.add(row[0])
    except Exception as e:
        log.exception(f"_existing_logged_urls query failed: {e}")
    return urls


async def job_search_log_check(db: Database) -> None:
    """Daily 08:00 UTC: read CK job_search_log.json, write atlas.tasks rows for new URLs.

    Track-state: compare seen_urls list against existing payload.kind='applicant_logged'
    rows. Write one row per untracked URL with payload={'kind':'applicant_logged',
    'url':<url>,'source':'job_search_log'}.

    Empty seen_urls is correct no-op (current state of the file).
    """
    log.info("job_search_log_check_start")
    log_data = await _read_job_search_log()
    if log_data is None:
        log.warning("job_search_log_check skipped: read failed")
        return
    seen_urls = log_data.get("seen_urls", [])
    if not isinstance(seen_urls, list):
        log.warning(f"job_search_log seen_urls not a list: {type(seen_urls).__name__}")
        return
    if not seen_urls:
        log.info("job_search_log_check_done seen=0 new=0 written=0 (empty seen_urls)")
        return

    existing = await _existing_logged_urls(db)
    new_urls = [u for u in seen_urls if isinstance(u, str) and u not in existing]
    written = 0
    for url in new_urls:
        task_id = await _create_monitoring_task(
            db,
            "applicant_logged",
            {"url": url, "source": "job_search_log"},
        )
        if task_id:
            written += 1
    log.info(
        f"job_search_log_check_done seen={len(seen_urls)} "
        f"new={len(new_urls)} written={written}"
    )


async def weekly_digest_compile(db: Database) -> None:
    """Mondays 07:00 local: aggregate last-7-day applicant_logged rows into one summary row.

    Writes one summary atlas.tasks row with payload={'kind':'weekly_digest_talent',
    'count':N,'urls':[...],'window_days':7}.

    Status-agnostic on input rows (pending/running/done all count toward the digest).
    """
    log.info("weekly_digest_compile_start")
    urls: list[str] = []
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT payload->>'url' FROM atlas.tasks "
                    "WHERE payload->>'kind' = 'applicant_logged' "
                    "AND created_at >= now() - interval '7 days' "
                    "AND payload->>'url' IS NOT NULL "
                    "ORDER BY created_at DESC"
                )
                rows = await cur.fetchall()
                urls = [r[0] for r in rows if r[0]]
    except Exception as e:
        log.exception(f"weekly_digest_compile aggregation query failed: {e}")
        return

    task_id = await _create_monitoring_task(
        db,
        "weekly_digest_talent",
        {"count": len(urls), "urls": urls, "window_days": 7},
    )
    log.info(
        f"weekly_digest_compile_done count={len(urls)} task_id={task_id}"
    )


async def recruiter_watch() -> None:
    """STUB: deferred to Atlas v0.1.1 pending Gmail integration.

    TODO(v0.1.1): wire Gmail OAuth + label scan for recruiter outreach;
    write atlas.tasks rows with payload.kind='recruiter_outreach' on inbound.
    """
    log.debug("recruiter_watch v0.1 stub (no-op)")
