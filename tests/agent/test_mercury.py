"""Atlas v0.1 Phase 8 -- Unit tests for atlas.agent.domains.mercury Phase 6 surface.

NEW file complementary to existing tests/agent/test_mercury_phase7.py (Phase 7.2
cancel-window). This file covers the Phase 6 supervision functions:
- mercury_liveness_check (every 5min)
- mercury_trade_activity_check (daily 08:00 UTC)
- mercury_real_money_failclosed (every 5min) + _check_ratification_doc gate

7 cases per paco_directive_atlas_v0_1_phase8.md section 2.9 (cases ADAPTED to ground-truth
surface per Path B; Sloan Day 78 evening):

1. test_mercury_liveness_active_no_alert -- _mercury_is_active=True -> no _create_monitoring_task
2. test_mercury_liveness_inactive_raises_critical (ADAPTED kind='mercury_liveness_warning')
3. test_trade_activity_recent_no_alert -- recent_count>0 -> no alert
4. test_trade_activity_stale_emits_warn -- recent_count=0 -> kind='mercury_trade_activity_warning' warn
5. test_failclosed_query_error_writes_check_error_critical (ADAPTED -- query returns None; no re-raise; check_error alert)
6. test_ratification_doc_present_no_alert -- real_count>0 + doc=True -> no alert (gate satisfied)
7. test_ratification_doc_absent_emits_critical (ADAPTED kind='mercury_real_money_unauthorized')

ADAPTATION RATIONALE (preserves directive intent; matches actual implementation):
- Phase 6 surface uses _create_monitoring_task (atlas.tasks writes), not emit_event.
- _ck_python_query returns None on cross-host failure; no exceptions propagate.
- Fail-closed safety bias: query failure writes 'mercury_failclosed_check_error' alert.
- Kind names from actual code: 'mercury_liveness_warning', 'mercury_trade_activity_warning',
  'mercury_real_money_unauthorized', 'mercury_failclosed_check_error'.

Pure-mock; runs in CI. NO real network/SSH/DB calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.agent.domains import mercury


# -----------------------------------------------------------------------------
# 1. liveness check: active state -> no alert
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mercury_liveness_active_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """_mercury_is_active=(True, 'active') -> _create_monitoring_task NOT called."""
    monkeypatch.setattr(mercury, "_mercury_is_active", AsyncMock(return_value=(True, "active")))
    monkeypatch.setattr(mercury, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(mercury, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await mercury.mercury_liveness_check(stub_db)

    assert mock_create.call_count == 0, (
        f"expected 0 _create_monitoring_task calls when active; got {mock_create.call_count}"
    )


# -----------------------------------------------------------------------------
# 2. liveness check: inactive -> Tier 3 critical (kind='mercury_liveness_warning')
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mercury_liveness_inactive_raises_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    """_mercury_is_active=(False, 'inactive') -> _create_monitoring_task with severity='critical'."""
    monkeypatch.setattr(mercury, "_mercury_is_active", AsyncMock(return_value=(False, "inactive")))
    monkeypatch.setattr(mercury, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(mercury, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await mercury.mercury_liveness_check(stub_db)

    assert mock_create.call_count == 1, f"expected 1 alert; got {mock_create.call_count}"
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "mercury_liveness_warning", f"expected kind='mercury_liveness_warning'; got {kind!r}"
    assert payload["severity"] == "critical"
    assert payload["systemctl_state"] == "inactive"
    assert payload["service"] == mercury.MERCURY_SERVICE
    assert payload["host"] == mercury.CK_HOST


# -----------------------------------------------------------------------------
# 3. trade activity: recent_count>0 -> no alert
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_activity_recent_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """mercury active + recent trades within window -> no alert."""
    monkeypatch.setattr(mercury, "_mercury_is_active", AsyncMock(return_value=(True, "active")))
    monkeypatch.setattr(mercury, "_ck_python_query", AsyncMock(return_value={
        "latest_closed": "2026-05-01 12:00:00+00",
        "recent_count": "5",
        "total": "100",
    }))
    monkeypatch.setattr(mercury, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(mercury, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await mercury.mercury_trade_activity_check(stub_db)

    assert mock_create.call_count == 0, (
        f"expected 0 alerts when recent_count=5; got {mock_create.call_count}"
    )


# -----------------------------------------------------------------------------
# 4. trade activity: recent_count=0 + active -> warn
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_activity_stale_emits_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """mercury active + zero recent trades -> _create_monitoring_task with severity='warn'."""
    monkeypatch.setattr(mercury, "_mercury_is_active", AsyncMock(return_value=(True, "active")))
    monkeypatch.setattr(mercury, "_ck_python_query", AsyncMock(return_value={
        "latest_closed": "2026-04-15 12:00:00+00",
        "recent_count": "0",
        "total": "100",
    }))
    monkeypatch.setattr(mercury, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(mercury, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await mercury.mercury_trade_activity_check(stub_db)

    assert mock_create.call_count == 1, f"expected 1 alert; got {mock_create.call_count}"
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "mercury_trade_activity_warning", (
        f"expected kind='mercury_trade_activity_warning'; got {kind!r}"
    )
    assert payload["severity"] == "warn"
    assert payload["recent_7d_count"] == 0
    assert payload["latest_closed"] == "2026-04-15 12:00:00+00"
    assert payload["gap_days_threshold"] == mercury.TRADE_ACTIVITY_GAP_DAYS


# -----------------------------------------------------------------------------
# 5. fail-closed on query error -> check_error critical (no re-raise)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failclosed_query_error_writes_check_error_critical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ck_python_query returns None (failure) -> 'mercury_failclosed_check_error' alert (fail-closed bias)."""
    monkeypatch.setattr(mercury, "_ck_python_query", AsyncMock(return_value=None))
    monkeypatch.setattr(mercury, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(mercury, "_create_monitoring_task", mock_create)
    # _check_ratification_doc must NOT be reached (function returns early on query failure)
    mock_doc = AsyncMock(return_value=True)
    monkeypatch.setattr(mercury, "_check_ratification_doc", mock_doc)

    stub_db = MagicMock()
    await mercury.mercury_real_money_failclosed(stub_db)

    assert mock_create.call_count == 1, f"expected 1 check_error alert; got {mock_create.call_count}"
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "mercury_failclosed_check_error", (
        f"expected kind='mercury_failclosed_check_error'; got {kind!r}"
    )
    assert payload["severity"] == "critical"
    assert "safety_bias" in payload
    assert "fail-closed" in payload["safety_bias"].lower()
    # Critical: ratification doc check must NOT be reached when query fails
    assert mock_doc.call_count == 0, (
        f"_check_ratification_doc should not be called when _ck_python_query returns None; got {mock_doc.call_count}"
    )


# -----------------------------------------------------------------------------
# 6. ratification doc present + real_count>0 -> no alert (gate satisfied)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ratification_doc_present_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """real_count>0 + _check_ratification_doc=True -> NO _create_monitoring_task call."""
    monkeypatch.setattr(mercury, "_ck_python_query", AsyncMock(return_value={
        "real_count": "3",
        "latest_real_open": "2026-05-01 12:00:00+00",
        "earliest_real_open": "2026-04-25 12:00:00+00",
    }))
    monkeypatch.setattr(mercury, "_check_ratification_doc", AsyncMock(return_value=True))
    monkeypatch.setattr(mercury, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(mercury, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await mercury.mercury_real_money_failclosed(stub_db)

    assert mock_create.call_count == 0, (
        f"expected 0 alerts when ratification doc present (gate satisfied); got {mock_create.call_count}"
    )


# -----------------------------------------------------------------------------
# 7. ratification doc absent + real_count>0 -> Tier 3 critical (unauthorized)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ratification_doc_absent_emits_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    """real_count>0 + _check_ratification_doc=False -> 'mercury_real_money_unauthorized' critical."""
    monkeypatch.setattr(mercury, "_ck_python_query", AsyncMock(return_value={
        "real_count": "3",
        "latest_real_open": "2026-05-01 12:00:00+00",
        "earliest_real_open": "2026-04-25 12:00:00+00",
    }))
    monkeypatch.setattr(mercury, "_check_ratification_doc", AsyncMock(return_value=False))
    monkeypatch.setattr(mercury, "_alert_already_today", AsyncMock(return_value=False))
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(mercury, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await mercury.mercury_real_money_failclosed(stub_db)

    assert mock_create.call_count == 1, f"expected 1 unauthorized alert; got {mock_create.call_count}"
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "mercury_real_money_unauthorized", (
        f"expected kind='mercury_real_money_unauthorized'; got {kind!r}"
    )
    assert payload["severity"] == "critical"
    assert payload["real_count"] == 3
    assert payload["ratification_doc_state"] == "absent"
    assert payload["ratification_doc_path"] == mercury.RATIFICATION_DOC_PATH
    assert "requires_action" in payload
