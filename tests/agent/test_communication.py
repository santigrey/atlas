"""Atlas v0.1 Phase 7 -- Unit tests for communication.py.

7 cases per paco_directive_atlas_v0_1_phase7.md section 2.5:
1. test_emit_event_severity_validation -- ValueError on bogus severity
2. test_emit_event_inserts_atlas_events -- INSERT row appears with payload-incl-severity-and-tier
3. test_emit_event_tier_mapping -- info->1, warn->2, critical->3 inside payload
4. test_emit_event_critical_calls_dispatch -- dispatch_telegram called once on critical only
5. test_dispatch_telegram_mock_mode -- TWILIO_ENABLED=false logs telegram_mock; no httpx
6. test_dispatch_telegram_missing_env -- TWILIO_ENABLED=true but env unset -> log.warning + no httpx
7. test_dispatch_telegram_real_post -- TWILIO_ENABLED=true + full env -> httpx POST with correct URL+auth

DB-backed tests (2, 3) use the real test DB matching atlas convention
(tests/db/test_db_smoke.py + test_cross_schema_read.py). Monkey-patching
async context managers in psycopg pool is fragile; INSERT+SELECT-readback is more
robust and exercises the same SQL the production path uses.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.agent import communication
from atlas.agent.communication import (
    dispatch_telegram,
    emit_event,
    _SEVERITY_TIER,
    _twilio_enabled,
)
from atlas.db import Database


# -----------------------------------------------------------------------------
# 1. severity validation
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_event_severity_validation() -> None:
    """emit_event with severity not in {info, warn, critical} raises ValueError."""
    db = Database()
    await db.open()
    try:
        with pytest.raises(ValueError, match="severity must be one of"):
            await emit_event(
                db,
                source="atlas.test",
                kind="test_kind",
                severity="bogus",
                payload={},
            )
    finally:
        await db.close()


# -----------------------------------------------------------------------------
# 2. INSERT writes correct row to atlas.events
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_event_inserts_atlas_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """emit_event inserts row with source/kind + payload (incl severity + tier)."""
    # Force mock-mode so critical does not actually attempt Twilio
    monkeypatch.delenv("TWILIO_ENABLED", raising=False)
    db = Database()
    await db.open()
    test_kind = "phase7_test_insert"
    try:
        await emit_event(
            db,
            source="atlas.phase7_test",
            kind=test_kind,
            severity="info",
            payload={"foo": "bar", "n": 42},
        )
        # Read back the row
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT source, kind, payload FROM atlas.events "
                    "WHERE kind = %s ORDER BY id DESC LIMIT 1",
                    (test_kind,),
                )
                row = await cur.fetchone()
                assert row is not None, "INSERT did not create row"
                source, kind, payload = row
                assert source == "atlas.phase7_test"
                assert kind == test_kind
                assert payload["foo"] == "bar"
                assert payload["n"] == 42
                assert payload["severity"] == "info"
                assert payload["tier"] == 1
                # Cleanup
                await cur.execute("DELETE FROM atlas.events WHERE kind = %s", (test_kind,))
                await conn.commit()
    finally:
        await db.close()


# -----------------------------------------------------------------------------
# 3. tier mapping (parametrized across 3 severities)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("severity,expected_tier", [("info", 1), ("warn", 2), ("critical", 3)])
async def test_emit_event_tier_mapping(
    monkeypatch: pytest.MonkeyPatch, severity: str, expected_tier: int
) -> None:
    """severity 'info'->tier=1; 'warn'->tier=2; 'critical'->tier=3 in payload."""
    # Mock dispatch_telegram so critical does not block on httpx/Twilio
    mock_dispatch = AsyncMock()
    monkeypatch.setattr(communication, "dispatch_telegram", mock_dispatch)
    monkeypatch.delenv("TWILIO_ENABLED", raising=False)
    db = Database()
    await db.open()
    test_kind = f"phase7_tier_map_{severity}"
    try:
        await emit_event(
            db,
            source="atlas.phase7_test",
            kind=test_kind,
            severity=severity,
            payload={"k": "v"},
        )
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT payload FROM atlas.events WHERE kind = %s ORDER BY id DESC LIMIT 1",
                    (test_kind,),
                )
                row = await cur.fetchone()
                assert row is not None
                payload = row[0]
                assert payload["severity"] == severity
                assert payload["tier"] == expected_tier
                # Confirm assertion lines up with module constant
                assert _SEVERITY_TIER[severity] == expected_tier
                # Cleanup
                await cur.execute("DELETE FROM atlas.events WHERE kind = %s", (test_kind,))
                await conn.commit()
    finally:
        await db.close()


# -----------------------------------------------------------------------------
# 4. dispatch_telegram called only on critical
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("severity,should_call", [("info", False), ("warn", False), ("critical", True)])
async def test_emit_event_critical_calls_dispatch(
    monkeypatch: pytest.MonkeyPatch, severity: str, should_call: bool
) -> None:
    """dispatch_telegram called once on critical; NOT called on info|warn."""
    mock_dispatch = AsyncMock()
    monkeypatch.setattr(communication, "dispatch_telegram", mock_dispatch)
    monkeypatch.delenv("TWILIO_ENABLED", raising=False)
    db = Database()
    await db.open()
    test_kind = f"phase7_dispatch_gate_{severity}"
    try:
        await emit_event(
            db,
            source="atlas.phase7_test",
            kind=test_kind,
            severity=severity,
            payload={"x": 1},
        )
        if should_call:
            assert mock_dispatch.call_count == 1, f"expected 1 dispatch call on critical; got {mock_dispatch.call_count}"
            # Verify message includes [CRITICAL atlas.<source>] prefix
            msg = mock_dispatch.call_args.args[0]
            assert msg.startswith("[CRITICAL atlas.atlas.phase7_test] "), f"unexpected msg prefix: {msg[:80]!r}"
            assert test_kind in msg
        else:
            assert mock_dispatch.call_count == 0, f"dispatch_telegram should NOT be called on {severity}; got {mock_dispatch.call_count}"
    finally:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM atlas.events WHERE kind = %s", (test_kind,))
                await conn.commit()
        await db.close()


# -----------------------------------------------------------------------------
# 5. dispatch_telegram mock mode
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_telegram_mock_mode(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """TWILIO_ENABLED=false (default) -> log telegram_mock; no httpx call."""
    monkeypatch.delenv("TWILIO_ENABLED", raising=False)
    # Spy on httpx.AsyncClient -- if invoked, fails the test
    mock_client = MagicMock()
    monkeypatch.setattr("atlas.agent.communication.httpx.AsyncClient", mock_client)
    import logging
    with caplog.at_level(logging.INFO, logger="atlas.agent.communication"):
        await dispatch_telegram("hello phase7 mock")
    # No httpx instantiation
    assert not mock_client.called, "httpx.AsyncClient should NOT be instantiated in mock-mode"
    # Log line captured
    messages = [r.message for r in caplog.records]
    assert any("telegram_mock" in m and "hello phase7 mock" in m for m in messages), \
        f"telegram_mock log line missing; saw: {messages}"


# -----------------------------------------------------------------------------
# 6. dispatch_telegram missing env in real mode
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_telegram_missing_env(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """TWILIO_ENABLED=true but env vars missing -> log.warning + no httpx; no crash."""
    monkeypatch.setenv("TWILIO_ENABLED", "true")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)
    monkeypatch.delenv("SLOAN_PHONE_NUMBER", raising=False)
    mock_client = MagicMock()
    monkeypatch.setattr("atlas.agent.communication.httpx.AsyncClient", mock_client)
    import logging
    with caplog.at_level(logging.WARNING, logger="atlas.agent.communication"):
        await dispatch_telegram("missing env case")
    assert not mock_client.called, "httpx.AsyncClient should NOT be instantiated when env missing"
    messages = [r.message for r in caplog.records]
    assert any("telegram_disabled_missing_env" in m for m in messages), \
        f"missing_env warning missing; saw: {messages}"


# -----------------------------------------------------------------------------
# 7. dispatch_telegram real-mode posts to Twilio API with correct URL+auth
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_telegram_real_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """TWILIO_ENABLED=true + full env -> POST to /Accounts/{sid}/Messages.json with Basic auth."""
    monkeypatch.setenv("TWILIO_ENABLED", "1")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_TEST_SID_PLACEHOLDER")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "TEST_TOKEN_PLACEHOLDER")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15555550100")
    monkeypatch.setenv("SLOAN_PHONE_NUMBER", "+15555550199")

    # Build a context-manager-aware mock for httpx.AsyncClient
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.text = "{}"
    mock_response.json = MagicMock(return_value={"sid": "SM_RESP_TEST"})
    mock_post = AsyncMock(return_value=mock_response)

    class MockAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.post = mock_post
        async def __aenter__(self) -> "MockAsyncClient":
            return self
        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("atlas.agent.communication.httpx.AsyncClient", MockAsyncClient)
    await dispatch_telegram("hello real-mode test")

    assert mock_post.call_count == 1, f"expected 1 POST; got {mock_post.call_count}"
    # URL contains the SID + correct path
    call_args = mock_post.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
    assert "AC_TEST_SID_PLACEHOLDER" in url
    assert url.endswith("/Messages.json")
    assert url.startswith("https://api.twilio.com/")
    # Basic auth header present + base64-encoded
    headers = call_args.kwargs["headers"]
    assert headers["Authorization"].startswith("Basic ")
    import base64
    expected_b64 = base64.b64encode(b"AC_TEST_SID_PLACEHOLDER:TEST_TOKEN_PLACEHOLDER").decode()
    assert headers["Authorization"] == f"Basic {expected_b64}"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    # Body contains URL-encoded From, To, Body
    body = call_args.kwargs["content"]
    assert "From=%2B15555550100" in body
    assert "To=%2B15555550199" in body
    assert "Body=hello+real-mode+test" in body


# -----------------------------------------------------------------------------
# Bonus: _twilio_enabled env-parsing sanity
# -----------------------------------------------------------------------------

def test_twilio_enabled_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """_twilio_enabled accepts {1, true, yes} as truthy; default false."""
    for true_val in ("1", "true", "True", "TRUE", "yes", "YES"):
        monkeypatch.setenv("TWILIO_ENABLED", true_val)
        assert _twilio_enabled() is True, f"{true_val!r} should be truthy"
    for false_val in ("0", "false", "False", "no", ""):
        monkeypatch.setenv("TWILIO_ENABLED", false_val)
        assert _twilio_enabled() is False, f"{false_val!r} should be falsy"
    monkeypatch.delenv("TWILIO_ENABLED", raising=False)
    assert _twilio_enabled() is False, "default should be False"
