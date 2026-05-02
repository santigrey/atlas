"""Atlas Operations Agent -- Domain 4: Mercury supervision.

Four functions per spec lines 395-447 + Atlas SOP v1.0:
- mercury_liveness_check: every 5min; SSH CK + systemctl is-active mercury-scanner.service;
  Tier 3 critical alert if NOT active (Mercury down = capital protection concern).
- mercury_trade_activity_check: daily 08:00 UTC; cross-host PG read of mercury.trades;
  Tier 2 warn if mercury-scanner active but no trades in last 7 days.
- mercury_real_money_failclosed: every 5min; cross-host PG read for paper_trade=false;
  Tier 3 critical IMMEDIATELY unless ratification doc exists at canonical CK path.
- mercury_start / mercury_stop: STUB at v0.1 (Paco-preferred option a); TODO Phase 7.

Cross-host architecture (Path B refined; ratified Day 78 morning):
- atlas runs on Beast; mercury.* schema lives on CK Postgres (192.168.1.10).
- Beast does NOT have CK PG credentials.
- Atlas SSHes to CK + runs `/usr/bin/python3 -c <inline-source>` with psycopg2.
- The inline source reads mercury's existing .env DATABASE_URL on CK; auths locally.
- Credential never leaves CK.
- shlex.quote() handles shell-escaping of the Python source.

Per amended spec (Phase 3 close substrate-gap preamble): all atlas-side writes go to
atlas.tasks via _create_monitoring_task. v0.1.1 will migrate Domain 1-4 writes to atlas.events.

P6 #32 reuse pattern (standing practice from Phase 4): _create_monitoring_task + _ssh_run
imported directly from infrastructure.py. _alert_already_today imported from vendor.py.

P6 #29 verified at write time (Step 1 probe):
- CK has /usr/bin/python3 3.10 + psycopg2 2.9.11 system-wide.
- mercury's .env at /home/jes/polymarket-ai-trader/.env contains parseable DATABASE_URL.
- CK does NOT have psql command (use Python+psycopg2 instead).
- mercury.trades schema verified live; columns include closed_at + paper_trade.
- Beast->CK SSH BatchMode auth confirmed working (Phase 4 carryover).
- Ratification doc absent at canonical CK path (correct baseline).

Fail-closed safety bias: if any check encounters an error reaching CK, mercury_real_money_failclosed
DEFAULTS to 'doc absent + treat as if real-money detected' (raise critical). Better to
false-positive an alert than silently skip the gate.

All probes READ-ONLY: SSH systemctl status read; SQL SELECTs on mercury.trades; `test -f`
for ratification doc; never mutate mercury data, mercury config, or ratification doc state.

Tier mapping (per Atlas SOP v1.0 Section 3.2):
- Tier 2 / severity='warn'     = trade activity gap >7 days
- Tier 3 / severity='critical' = mercury-scanner down OR real-money trades without ratification

Weak credential note (P5 candidate Atlas v0.1.1): Mercury .env on CK contains
DATABASE_URL with literal 'adminpass' password. Pre-existing state, not introduced
by Phase 6. Atlas v0.1.1 hardening cycle should rotate to strong password +
introduce read-only mercury_reader role.
"""

from __future__ import annotations

import json
import logging
import shlex
from datetime import datetime, timezone
from typing import Any, Optional

from atlas.db import Database
from atlas.agent.domains.infrastructure import _create_monitoring_task, _ssh_run
from atlas.agent.domains.vendor import _alert_already_today

log = logging.getLogger(__name__)

# Cross-host coordinates
CK_HOST = "192.168.1.10"
CK_USER = "jes"
MERCURY_SERVICE = "mercury-scanner.service"
MERCURY_ENV_PATH = "/home/jes/polymarket-ai-trader/.env"
RATIFICATION_DOC_PATH = "/home/jes/control-plane/docs/mercury_real_money_ratification.md"

# Trade activity threshold
TRADE_ACTIVITY_GAP_DAYS = 7

# Static read-only SQL queries (PD-authored module-level constants; no user input).
# Using ::text cast on timestamps to ensure stable JSON-serializable string output
# from the SSH+inline-python helper.
SQL_TRADE_ACTIVITY = (
    "SELECT max(closed_at)::text AS latest_closed, "
    "count(*) FILTER (WHERE closed_at > now() - interval '7 days') AS recent_count, "
    "count(*) AS total "
    "FROM mercury.trades"
)
SQL_REAL_MONEY_COUNT = (
    "SELECT count(*) AS real_count, "
    "max(opened_at)::text AS latest_real_open, "
    "min(opened_at)::text AS earliest_real_open "
    "FROM mercury.trades WHERE paper_trade = false"
)

# Inline Python template that runs on CK; reads mercury's .env DATABASE_URL, executes the SQL
# (interpolated in via repr), prints JSON of the first row to stdout.
# The %s-shaped placeholder {sql_repr} is substituted via str.format BEFORE shlex.quote;
# the SQL is repr()'d so its quoting is safe inside the Python source.
_CK_PY_TEMPLATE = '''\
import json, psycopg2
from pathlib import Path
env = {{}}
for line in Path({env_path_repr}).read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip(chr(34)).strip(chr(39))
conn = psycopg2.connect(env["DATABASE_URL"])
cur = conn.cursor()
cur.execute({sql_repr})
cols = [c.name for c in cur.description]
row = cur.fetchone()
print(json.dumps(dict(zip(cols, [str(v) if v is not None else None for v in row]))))
conn.close()
'''


async def _ck_python_query(sql: str, timeout: float = 15.0) -> Optional[dict[str, Any]]:
    """Run a read-only SELECT on CK Postgres via SSH + inline psycopg2.

    Returns the first row as a dict on success, None on failure. Logs full error
    detail. SQL is repr-interpolated into a Python source template; intended only
    for static module-level query constants (PD-authored), NOT user input.
    """
    py_src = _CK_PY_TEMPLATE.format(
        env_path_repr=repr(MERCURY_ENV_PATH),
        sql_repr=repr(sql),
    )
    cmd = f"/usr/bin/python3 -c {shlex.quote(py_src)}"
    rc, stdout, stderr = await _ssh_run(CK_HOST, CK_USER, cmd, timeout=timeout)
    if rc != 0:
        log.warning(f"_ck_python_query failed rc={rc} stderr={stderr[:300]}")
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        log.warning(f"_ck_python_query JSON parse failed: {e}; stdout={stdout[:200]}")
        return None


async def _check_ratification_doc() -> Optional[bool]:
    """SSH to CK; check if mercury_real_money_ratification.md exists at canonical path.

    Returns True if present, False if absent, None on SSH error.
    Caller treats None as 'absent' for fail-closed safety bias.
    """
    cmd = f"test -f {shlex.quote(RATIFICATION_DOC_PATH)} && echo PRESENT || echo ABSENT"
    rc, stdout, stderr = await _ssh_run(CK_HOST, CK_USER, cmd, timeout=10.0)
    if rc != 0:
        log.warning(f"_check_ratification_doc SSH failed rc={rc} stderr={stderr[:200]}")
        return None
    out = stdout.strip()
    if out == "PRESENT":
        return True
    if out == "ABSENT":
        return False
    log.warning(f"_check_ratification_doc unexpected output: {out!r}")
    return None


async def _mercury_is_active() -> tuple[bool, str]:
    """SSH to CK; run systemctl is-active mercury-scanner.service.

    Returns (is_active, raw_state). raw_state is the systemctl output (active, inactive,
    failed, activating, etc) -- captured for payload context. SSH failure -> (False, 'unknown').
    """
    cmd = f"systemctl is-active {shlex.quote(MERCURY_SERVICE)}"
    rc, stdout, stderr = await _ssh_run(CK_HOST, CK_USER, cmd, timeout=10.0)
    raw = stdout.strip()
    # systemctl is-active returns 0 only when state is 'active'. Other states return non-zero.
    if rc == 0 and raw == "active":
        return True, raw
    if rc != 0 and not raw:
        log.warning(f"_mercury_is_active SSH failed rc={rc} stderr={stderr[:200]}")
        return False, "unknown"
    return False, raw


async def mercury_liveness_check(db: Database) -> None:
    """Every 5min: SSH CK + systemctl is-active mercury-scanner.service.

    Tier 3 critical alert if NOT active. Per-day dedup so a single down-day produces
    one alert (not 288). When mercury comes back up, next day's check confirms restoration.
    """
    log.info("mercury_liveness_check_start")
    is_active, raw_state = await _mercury_is_active()
    if is_active:
        log.info(f"mercury_liveness_check_done state={raw_state} (no alert)")
        return

    severity = "critical"
    if await _alert_already_today(db, "mercury_liveness_warning", severity=severity):
        log.debug(f"mercury_liveness_check skip dup state={raw_state}")
        return

    task_id = await _create_monitoring_task(
        db,
        "mercury_liveness_warning",
        {
            "service": MERCURY_SERVICE,
            "host": CK_HOST,
            "systemctl_state": raw_state,
            "severity": severity,
        },
    )
    log.warning(f"mercury_liveness_check ALERT state={raw_state} task_id={task_id}")


async def mercury_trade_activity_check(db: Database) -> None:
    """Daily 08:00 UTC: cross-host query mercury.trades; alert if no trades in last 7 days.

    Only alerts when mercury-scanner is currently ACTIVE -- if it's down, liveness_check
    handles that case. The two checks compose: liveness covers 'service down', activity
    covers 'service up but not trading'.
    """
    log.info("mercury_trade_activity_check_start")
    is_active, raw_state = await _mercury_is_active()
    if not is_active:
        log.info(
            f"mercury_trade_activity_check skipped: mercury-scanner not active (state={raw_state}); liveness_check covers this"
        )
        return

    result = await _ck_python_query(SQL_TRADE_ACTIVITY)
    if result is None:
        log.warning("mercury_trade_activity_check skipped: cross-host query failed")
        return

    latest_closed = result.get("latest_closed")
    recent_count_raw = result.get("recent_count")
    total = result.get("total")
    try:
        recent_count = int(recent_count_raw) if recent_count_raw is not None else 0
    except (TypeError, ValueError):
        recent_count = 0

    if recent_count > 0:
        log.info(
            f"mercury_trade_activity_check_done recent_7d={recent_count} total={total} latest={latest_closed} (no alert)"
        )
        return

    # Gap detected: mercury-scanner is active but zero trades in last 7 days
    severity = "warn"
    if await _alert_already_today(db, "mercury_trade_activity_warning", severity=severity):
        log.debug("mercury_trade_activity_check skip dup")
        return

    task_id = await _create_monitoring_task(
        db,
        "mercury_trade_activity_warning",
        {
            "service": MERCURY_SERVICE,
            "host": CK_HOST,
            "latest_closed": latest_closed,
            "recent_7d_count": recent_count,
            "total_trades": total,
            "gap_days_threshold": TRADE_ACTIVITY_GAP_DAYS,
            "severity": severity,
        },
    )
    log.warning(
        f"mercury_trade_activity_check ALERT recent_7d=0 latest={latest_closed} task_id={task_id}"
    )


async def mercury_real_money_failclosed(db: Database) -> None:
    """Every 5min: cross-host query mercury.trades for paper_trade=false rows.

    If real-money trades detected AND ratification doc absent -> Tier 3 critical alert.
    If real-money trades detected AND ratification doc present -> log info, no alert (gate satisfied).
    If zero real-money trades -> log info, no alert (baseline).

    Per-day dedup with fail-open: if dedup query fails, write the alert anyway (better
    to spam critical alerts than silently miss them).

    Fail-closed safety bias on cross-host failure: if SSH or query fails, we cannot
    confirm real_count=0; assume real_count>0 + treat ratification as absent + raise
    a separate 'check failed' Tier 3 alert. The capital-protection gate must err toward
    suspicion.
    """
    log.info("mercury_real_money_failclosed_start")
    result = await _ck_python_query(SQL_REAL_MONEY_COUNT)
    if result is None:
        # Cross-host query failed -- fail-closed with separate alert kind
        if await _alert_already_today(db, "mercury_failclosed_check_error", severity="critical"):
            log.debug("mercury_real_money_failclosed skip dup (check_error)")
            return
        task_id = await _create_monitoring_task(
            db,
            "mercury_failclosed_check_error",
            {
                "reason": "cross-host query to CK mercury.trades failed; cannot verify real-money state",
                "severity": "critical",
                "safety_bias": "fail-closed: assume worst case until check restored",
            },
        )
        log.error(f"mercury_real_money_failclosed CHECK FAILED task_id={task_id}")
        return

    real_count_raw = result.get("real_count")
    try:
        real_count = int(real_count_raw) if real_count_raw is not None else 0
    except (TypeError, ValueError):
        real_count = 0

    if real_count == 0:
        log.info("mercury_real_money_failclosed_done real_count=0 (gate naturally satisfied)")
        return

    # Real-money trades detected; check ratification
    doc_present = await _check_ratification_doc()
    if doc_present is True:
        log.info(
            f"mercury_real_money_failclosed_done real_count={real_count} ratification_doc=PRESENT (gate satisfied by CEO ratification)"
        )
        return

    # doc_present is False (absent) OR None (check failed) -- both are fail-closed cases
    doc_state = "absent" if doc_present is False else "check_failed"
    severity = "critical"
    if await _alert_already_today(db, "mercury_real_money_unauthorized", severity=severity):
        log.debug("mercury_real_money_failclosed skip dup")
        return

    task_id = await _create_monitoring_task(
        db,
        "mercury_real_money_unauthorized",
        {
            "real_count": real_count,
            "latest_real_open": result.get("latest_real_open"),
            "earliest_real_open": result.get("earliest_real_open"),
            "ratification_doc_state": doc_state,
            "ratification_doc_path": RATIFICATION_DOC_PATH,
            "severity": severity,
            "requires_action": "CEO must author ratification doc OR mercury must be reverted to paper-trade-only",
        },
    )
    log.error(
        f"mercury_real_money_failclosed FAIL-CLOSED ALERT real_count={real_count} doc={doc_state} task_id={task_id}"
    )


async def mercury_start(db: Database) -> None:
    """STUB: invoke `ssh ck sudo systemctl start mercury-scanner.service`.

    v0.1: log-only stub. Not wired into any cadence. Will be invoked by future
    atlas.tasks claim with kind='mercury_control' once the dispatch path exists.
    Tier 2 cancel-window enforcement requires emit_event helper from Phase 7.

    TODO(Phase 7): implement real start with cancel-window via communication.py.
    """
    log.info("mercury_start v0.1 stub (no-op; Phase 7 wires real start with cancel-window)")


async def mercury_stop(db: Database) -> None:
    """STUB: invoke `ssh ck sudo systemctl stop mercury-scanner.service`.

    v0.1: log-only stub. Not wired into any cadence. Will be invoked by future
    atlas.tasks claim with kind='mercury_control' once the dispatch path exists.
    Tier 2 cancel-window enforcement requires emit_event helper from Phase 7.

    TODO(Phase 7): implement real stop with cancel-window via communication.py.
    """
    log.info("mercury_stop v0.1 stub (no-op; Phase 7 wires real stop with cancel-window)")
