"""Atlas v0.1 Phase 7 -- Communication helper.

Per paco_directive_atlas_v0_1_phase7.md (Day 78 mid-day) section 0 corrections:
- atlas.events has no severity column; severity carried in payload.severity (string)
  + payload.tier (int 1|2|3) per the auto-mapping below.
- Telegram dispatch is first-time integration; Twilio Programmable Messaging API
  via httpx; guarded by TWILIO_ENABLED env (default mock-mode).

Tier mapping (severity -> tier -> dispatch):
- 'info' -> Tier 1: atlas.events row only.
- 'warn' -> Tier 2: atlas.events row + downstream dashboard consumer
  reads the row (Phase 7 only writes).
- 'critical' -> Tier 3: atlas.events row + Telegram (real or mock
  per TWILIO_ENABLED).

Logging convention: stdlib `logging` with f-string interpolation to match
atlas.agent package convention (mercury.py, vendor.py, infrastructure.py, etc.).
Directive sketch used structlog kwarg style; adapted to stdlib f-string per
P6 #29 (API symbol verification: atlas.agent.* uses stdlib logging).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.parse
from typing import Any

import httpx

from atlas.db import Database

log = logging.getLogger(__name__)


# Tier mapping; severity -> tier int
_SEVERITY_TIER = {"info": 1, "warn": 2, "critical": 3}
_VALID_SEVERITIES = frozenset(_SEVERITY_TIER.keys())

# Twilio Messaging API endpoint template
_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def _twilio_enabled() -> bool:
    """Read TWILIO_ENABLED env. Default false (mock-mode)."""
    return os.getenv("TWILIO_ENABLED", "false").lower() in ("1", "true", "yes")


async def emit_event(
    db: Database,
    *,
    source: str,
    kind: str,
    severity: str,
    payload: dict[str, Any],
) -> None:
    """Write atlas.events row; auto-map severity to tier; dispatch on critical.

    Args:
        db: Atlas Database wrapper (psycopg_pool.AsyncConnectionPool).
        source: event source label (e.g. "atlas.mercury", "atlas.infrastructure").
        kind: event kind (e.g. "mercury_start_initiated", "monitoring_cpu_high").
        severity: "info" | "warn" | "critical" -- raises ValueError otherwise.
        payload: dict serialized into payload JSONB. Function adds "severity" and
            "tier" keys (overwriting any caller-supplied values for those keys).

    Side effects:
        - INSERTs one row into atlas.events.
        - On severity='critical': calls dispatch_telegram with a derived message.
        - On severity='warn': writes only (dashboard banner is a downstream consumer
          that reads atlas.events; Phase 7 does not push to dashboard directly).
        - On severity='info': writes only.

    Failure mode: if INSERT fails, logs error + raises (caller's fault tolerance).
        Telegram dispatch failure (httpx error or 4xx/5xx) is caught + logged but
        does NOT raise (atlas.events row already persisted; failure-to-page is non-fatal).
    """
    if severity not in _VALID_SEVERITIES:
        raise ValueError(
            f"emit_event: severity must be one of {sorted(_VALID_SEVERITIES)}, got {severity!r}"
        )
    tier = _SEVERITY_TIER[severity]
    enriched = dict(payload)  # shallow copy; do not mutate caller's dict
    enriched["severity"] = severity
    enriched["tier"] = tier
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO atlas.events (source, kind, payload) "
                "VALUES (%s, %s, %s::jsonb)",
                (source, kind, json.dumps(enriched, default=str)),
            )
            await conn.commit()
    log.info(
        f"emit_event source={source} kind={kind} severity={severity} tier={tier}"
    )
    if severity == "critical":
        # Build human-readable message; payload may contain extra context
        # (caller responsibility to keep payload non-secret-bearing)
        message = (
            f"[CRITICAL atlas.{source}] {kind}: "
            + json.dumps(payload, default=str)[:300]
        )
        try:
            await dispatch_telegram(message)
        except Exception as e:
            log.error(f"telegram_dispatch_failed kind={kind} error={e}")
            # Do not raise; atlas.events row already persisted.


async def dispatch_telegram(message: str) -> None:
    """Send Telegram (SMS) via Twilio Programmable Messaging API.

    If TWILIO_ENABLED is false (default), logs intended message and returns (mock mode).
    If TWILIO_ENABLED is true, requires:
        - TWILIO_ACCOUNT_SID
        - TWILIO_AUTH_TOKEN
        - TWILIO_FROM_NUMBER (Denver A2P 10DLC registered number)
        - SLOAN_PHONE_NUMBER (destination)
    Missing env in real-mode: log warning + return (do NOT crash).

    Idempotency: caller responsibility; this helper does no dedup.
    """
    if not _twilio_enabled():
        log.info(f"telegram_mock message={message}")
        return
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_n = os.getenv("TWILIO_FROM_NUMBER")
    to_n = os.getenv("SLOAN_PHONE_NUMBER")
    if not all([sid, token, from_n, to_n]):
        log.warning(
            f"telegram_disabled_missing_env has_sid={bool(sid)} "
            f"has_token={bool(token)} has_from={bool(from_n)} has_to={bool(to_n)}"
        )
        return
    url = _TWILIO_API.format(sid=sid)
    auth_b64 = base64.b64encode(f"{sid}:{token}".encode()).decode()
    body = (
        f"From={urllib.parse.quote_plus(from_n)}"
        f"&To={urllib.parse.quote_plus(to_n)}"
        f"&Body={urllib.parse.quote_plus(message)}"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=body,
        )
        if resp.status_code >= 400:
            log.error(
                f"telegram_http_error status={resp.status_code} body={resp.text[:200]}"
            )
            resp.raise_for_status()
        try:
            sid_out = resp.json().get("sid", "?")
        except Exception:
            sid_out = "?"
        log.info(f"telegram_sent sid={sid_out}")
