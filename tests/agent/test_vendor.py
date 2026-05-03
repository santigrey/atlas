"""Atlas v0.1 Phase 8 -- Unit tests for atlas.agent.domains.vendor.

5 cases per paco_directive_atlas_v0_1_phase8.md section 2.8:
1. test_renewal_warning_14d_threshold -- 13d -> warn; 15d -> no alert
2. test_renewal_warning_3d_threshold_critical -- 2d -> critical; 4d -> warn
3. test_tailscale_status_parses_self_key_expiry -- Self.KeyExpiry parsing + threshold (renamed; vendor.py reads Self.KeyExpiry, not a devices list)
4. test_github_pat_note_parses_expiry -- notes 'pat_expires_at:YYYY-MM-DD' parsed correctly
5. test_alert_already_today_dedup -- True when fetchone returns row; False when None

All 5 use a small mock DB helper (_MockDb / _MockConn / _MockCursor) for async context
manager semantics. NO real network/DB calls. Pure-mock; runs in CI.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.agent.domains import vendor


# -----------------------------------------------------------------------------
# Mock DB context-manager helpers
# -----------------------------------------------------------------------------

class _MockCursor:
    """Async context-manager cursor with controlled fetchall / fetchone results."""

    def __init__(self, fetchall_result=None, fetchone_result=None) -> None:
        self.execute = AsyncMock()
        self.fetchall = AsyncMock(return_value=fetchall_result if fetchall_result is not None else [])
        self.fetchone = AsyncMock(return_value=fetchone_result)

    async def __aenter__(self) -> "_MockCursor":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _MockConn:
    def __init__(self, cursor: _MockCursor) -> None:
        self._cursor = cursor
        self.commit = AsyncMock()

    def cursor(self) -> _MockCursor:
        return self._cursor

    async def __aenter__(self) -> "_MockConn":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _MockDb:
    def __init__(self, cursor: _MockCursor) -> None:
        self._conn = _MockConn(cursor)

    def connection(self) -> _MockConn:
        return self._conn


# -----------------------------------------------------------------------------
# 1. renewal_warning_14d_threshold
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_renewal_warning_14d_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """days_until=13 -> warn; days_until=15 -> no alert (outside 14-day window)."""
    today = datetime.now(tz=timezone.utc).date()
    rows = [
        ("test-vendor-13d", today + timedelta(days=13), Decimal("49.00")),
        ("test-vendor-15d", today + timedelta(days=15), Decimal("99.00")),
    ]
    mock_cursor = _MockCursor(fetchall_result=rows)
    mock_db = _MockDb(mock_cursor)
    # No dedup hits
    monkeypatch.setattr(vendor, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(vendor, "_create_monitoring_task", mock_create)

    await vendor.vendor_renewal_check(mock_db)

    # Only 13d should trigger; 15d outside both windows
    assert mock_create.call_count == 1, (
        f"expected 1 _create_monitoring_task call (13d only); got {mock_create.call_count}"
    )
    payload = mock_create.call_args.args[2]
    assert payload["vendor_name"] == "test-vendor-13d"
    assert payload["days_until"] == 13
    assert payload["severity"] == "warn", f"expected severity='warn'; got {payload['severity']!r}"
    assert payload["threshold"] == "14_day", f"expected threshold='14_day'; got {payload['threshold']!r}"


# -----------------------------------------------------------------------------
# 2. renewal_warning_3d_threshold_critical
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_renewal_warning_3d_threshold_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    """days_until=2 -> critical (Tier 3); days_until=4 -> warn (Tier 2)."""
    today = datetime.now(tz=timezone.utc).date()
    rows = [
        ("test-vendor-2d", today + timedelta(days=2), Decimal("19.00")),
        ("test-vendor-4d", today + timedelta(days=4), Decimal("29.00")),
    ]
    mock_cursor = _MockCursor(fetchall_result=rows)
    mock_db = _MockDb(mock_cursor)
    monkeypatch.setattr(vendor, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(vendor, "_create_monitoring_task", mock_create)

    await vendor.vendor_renewal_check(mock_db)

    # Both should trigger; 2d=critical, 4d=warn
    assert mock_create.call_count == 2, (
        f"expected 2 _create_monitoring_task calls; got {mock_create.call_count}"
    )
    by_name = {call.args[2]["vendor_name"]: call.args[2] for call in mock_create.call_args_list}
    assert by_name["test-vendor-2d"]["severity"] == "critical"
    assert by_name["test-vendor-2d"]["threshold"] == "3_day"
    assert by_name["test-vendor-2d"]["days_until"] == 2
    assert by_name["test-vendor-4d"]["severity"] == "warn"
    assert by_name["test-vendor-4d"]["threshold"] == "14_day"
    assert by_name["test-vendor-4d"]["days_until"] == 4


# -----------------------------------------------------------------------------
# 3. tailscale Self.KeyExpiry parse + threshold
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tailscale_status_parses_self_key_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """_local_run JSON with Self.KeyExpiry < 30 days -> _create_monitoring_task with payload fields."""
    # KeyExpiry 10 days from now -> below threshold 30 -> warn
    expiry_dt = datetime.now(tz=timezone.utc) + timedelta(days=10)
    expiry_str = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    tailscale_json = json.dumps({
        "Self": {
            "HostName": "beast",
            "ID": "node-test-id-12345",
            "KeyExpiry": expiry_str,
        }
    })
    mock_local = AsyncMock(return_value=(0, tailscale_json, ""))
    monkeypatch.setattr(vendor, "_local_run", mock_local)
    monkeypatch.setattr(vendor, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(vendor, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await vendor.tailscale_authkey_check(stub_db)

    assert mock_create.call_count == 1, f"expected 1 alert; got {mock_create.call_count}"
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "tailscale_authkey_warning"
    assert payload["host"] == "beast"
    assert payload["node_id"] == "node-test-id-12345"
    assert payload["key_expiry"] == expiry_str
    assert payload["severity"] == "warn"
    assert 9 <= payload["days_until"] <= 10  # ~10 days, allow rounding

    # Edge case: KeyExpiry > 30 days -> no alert
    mock_create.reset_mock()
    far_expiry = (datetime.now(tz=timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far_json = json.dumps({"Self": {"HostName": "beast", "ID": "x", "KeyExpiry": far_expiry}})
    monkeypatch.setattr(vendor, "_local_run", AsyncMock(return_value=(0, far_json, "")))
    await vendor.tailscale_authkey_check(stub_db)
    assert mock_create.call_count == 0, "expected no alert when expiry > 30 days"


# -----------------------------------------------------------------------------
# 4. GitHub PAT note parse
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_pat_note_parses_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """notes 'pat_expires_at:YYYY-MM-DD' (within 30 days) -> _create_monitoring_task with expiry_date."""
    today = datetime.now(tz=timezone.utc).date()
    expiry = today + timedelta(days=10)
    expiry_iso = expiry.isoformat()
    notes = f"GitHub vendor; pat_expires_at:{expiry_iso}; rotate annually"
    mock_cursor = _MockCursor(fetchone_result=(notes,))
    mock_db = _MockDb(mock_cursor)
    monkeypatch.setattr(vendor, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(vendor, "_create_monitoring_task", mock_create)

    await vendor.github_pat_check(mock_db)

    assert mock_create.call_count == 1, f"expected 1 alert; got {mock_create.call_count}"
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "github_pat_warning"
    assert payload["expiry_date"] == expiry_iso
    assert payload["days_until"] == 10
    assert payload["severity"] == "warn"

    # Edge case: no marker -> no alert
    mock_create.reset_mock()
    no_marker_cursor = _MockCursor(fetchone_result=("GitHub vendor; no expiry tracked",))
    no_marker_db = _MockDb(no_marker_cursor)
    await vendor.github_pat_check(no_marker_db)
    assert mock_create.call_count == 0, "no marker -> no alert expected"


# -----------------------------------------------------------------------------
# 5. _alert_already_today dedup helper
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alert_already_today_dedup() -> None:
    """_alert_already_today returns True when fetchone returns a row; False when None."""
    # True case: fetchone returns (1,)
    cursor_hit = _MockCursor(fetchone_result=(1,))
    db_hit = _MockDb(cursor_hit)
    result_hit = await vendor._alert_already_today(
        db_hit, "vendor_renewal_warning", vendor_name="acme", severity="warn"
    )
    assert result_hit is True, f"expected True on row hit; got {result_hit!r}"

    # False case: fetchone returns None
    cursor_miss = _MockCursor(fetchone_result=None)
    db_miss = _MockDb(cursor_miss)
    result_miss = await vendor._alert_already_today(
        db_miss, "vendor_renewal_warning", vendor_name="acme", severity="warn"
    )
    assert result_miss is False, f"expected False on no row; got {result_miss!r}"

    # Verify SQL was assembled with the expected predicates
    sql_called = cursor_hit.execute.call_args.args[0]
    args_called = cursor_hit.execute.call_args.args[1]
    assert "payload->>'kind' = %s" in sql_called
    assert "created_at >= %s" in sql_called
    assert "payload->>'vendor_name' = %s" in sql_called
    assert "payload->>'severity' = %s" in sql_called
    assert args_called[0] == "vendor_renewal_warning"
    assert args_called[2] == "acme"
    assert args_called[3] == "warn"
