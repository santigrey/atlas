"""Atlas v0.1 Phase 8 -- Unit tests for atlas.agent.domains.infrastructure.

7 cases per paco_directive_atlas_v0_1_phase8.md section 2.6.
Cases 4-7 ADAPTED to ground-truth surface per Path B (Sloan, Day 78 evening):

1. test_prometheus_query_parses_cpu_value -- _prom_first_value extracts CPU% from sample JSON
2. test_prometheus_query_parses_ram_value -- _prom_first_value extracts RAM% from sample JSON
3. test_ssh_fallback_invoked_when_prometheus_unreachable -- prom None -> _ssh_run + source='ssh'
4. test_threshold_detection_cpu_high (ADAPTED) -- cpu_pct > 85 -> payload.threshold_breach=True
5. test_threshold_detection_cpu_normal (ADAPTED) -- cpu_pct <= 85 -> payload.threshold_breach=False
6. test_substrate_anchor_unchanged (ADAPTED) -- _local_run returns canonical -> drift_detected=False
7. test_substrate_anchor_changed_raises_drift (ADAPTED) -- _local_run drifted -> drift_detected=True

ADAPTATION RATIONALE (preserves directive intent; matches actual implementation):
- infrastructure.py never imports/calls emit_event; persists findings via _create_monitoring_task
  to atlas.tasks (per Sloan directive Day 78 morning + canonical Cycle 1I create_task pattern).
- _create_monitoring_task is ALWAYS called for vitals; threshold_breach is a payload boolean.
- substrate_anchor_check uses _local_run (Beast-local docker inspect), not _ssh_run, and writes
  drift_detected=True/False; downstream consumer reacts (no direct Tier 3 emit at this layer).

All tests monkey-patch infrastructure module internals. NO real network/DB calls. Pure-mock; runs in CI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.agent.domains import infrastructure


# Sample Prometheus query response shape (vector result with single value)
def _prom_response(value: float) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"instance": "192.168.1.152:9100"},
                    "value": [1735689600.123, str(value)],
                }
            ],
        },
    }


TEST_NODE = {"name": "beast", "ip": "192.168.1.152", "user": "jes"}
TEST_INSTANCE = "192.168.1.152:9100"


# -----------------------------------------------------------------------------
# 1. parses CPU value
# -----------------------------------------------------------------------------

def test_prometheus_query_parses_cpu_value() -> None:
    """_prom_first_value extracts the numeric CPU% from a Prometheus vector response."""
    resp = _prom_response(42.5)
    cpu_pct = infrastructure._prom_first_value(resp)
    assert cpu_pct == 42.5, f"expected 42.5; got {cpu_pct!r}"


# -----------------------------------------------------------------------------
# 2. parses RAM value
# -----------------------------------------------------------------------------

def test_prometheus_query_parses_ram_value() -> None:
    """_prom_first_value extracts the numeric RAM% from a Prometheus vector response."""
    resp = _prom_response(67.3)
    ram_pct = infrastructure._prom_first_value(resp)
    assert ram_pct == 67.3, f"expected 67.3; got {ram_pct!r}"
    # Edge case: empty result -> None
    empty_resp = {"status": "success", "data": {"resultType": "vector", "result": []}}
    assert infrastructure._prom_first_value(empty_resp) is None
    # None input -> None
    assert infrastructure._prom_first_value(None) is None


# -----------------------------------------------------------------------------
# 3. SSH fallback invoked when Prometheus unreachable
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ssh_fallback_invoked_when_prometheus_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_prometheus_query returning None must trigger _ssh_run with the top fallback command."""
    # Simulate Prometheus unreachable -> _prometheus_query returns None
    mock_prom = AsyncMock(return_value=None)
    monkeypatch.setattr(infrastructure, "_prometheus_query", mock_prom)
    # _ssh_run returns parseable top output: 80% idle => 20% cpu
    top_output = "%Cpu(s):  5.0 us,  2.0 sy,  0.0 ni, 80.0 id,  0.0 wa, 0.0 hi, 0.0 si, 0.0 st"
    mock_ssh = AsyncMock(return_value=(0, top_output, ""))
    monkeypatch.setattr(infrastructure, "_ssh_run", mock_ssh)
    # Capture _create_monitoring_task calls
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(infrastructure, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    stub_client = MagicMock()
    await infrastructure._check_cpu(stub_db, stub_client, TEST_NODE, TEST_INSTANCE)

    # _ssh_run called once with expected args
    assert mock_ssh.call_count == 1, f"expected 1 _ssh_run call; got {mock_ssh.call_count}"
    ssh_args = mock_ssh.call_args.args
    assert ssh_args[0] == TEST_NODE["ip"], f"wrong host: {ssh_args[0]!r}"
    assert ssh_args[1] == TEST_NODE["user"], f"wrong user: {ssh_args[1]!r}"
    assert "top -bn1" in ssh_args[2], f"cmd missing top fallback: {ssh_args[2]!r}"
    # _create_monitoring_task called with source='ssh' AND cpu_pct=20.0 (100 - 80)
    assert mock_create.call_count == 1
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "monitoring_cpu", f"expected kind='monitoring_cpu'; got {kind!r}"
    assert payload["source"] == "ssh", f"expected source='ssh'; got {payload['source']!r}"
    assert payload["cpu_pct"] == 20.0, f"expected cpu_pct=20.0; got {payload['cpu_pct']!r}"


# -----------------------------------------------------------------------------
# 4. threshold detection cpu high (ADAPTED)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_threshold_detection_cpu_high(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU% > 85 -> _create_monitoring_task with payload.threshold_breach=True."""
    mock_prom = AsyncMock(return_value=_prom_response(92.5))
    monkeypatch.setattr(infrastructure, "_prometheus_query", mock_prom)
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(infrastructure, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    stub_client = MagicMock()
    await infrastructure._check_cpu(stub_db, stub_client, TEST_NODE, TEST_INSTANCE)

    assert mock_create.call_count == 1
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "monitoring_cpu"
    assert payload["cpu_pct"] == 92.5
    assert payload["source"] == "prometheus"
    assert payload["threshold_breach"] is True, (
        f"expected threshold_breach=True for cpu_pct=92.5 (>85); got {payload['threshold_breach']!r}"
    )


# -----------------------------------------------------------------------------
# 5. threshold detection cpu normal (ADAPTED)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_threshold_detection_cpu_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU% <= 85 -> _create_monitoring_task with payload.threshold_breach=False."""
    mock_prom = AsyncMock(return_value=_prom_response(50.0))
    monkeypatch.setattr(infrastructure, "_prometheus_query", mock_prom)
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(infrastructure, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    stub_client = MagicMock()
    await infrastructure._check_cpu(stub_db, stub_client, TEST_NODE, TEST_INSTANCE)

    assert mock_create.call_count == 1
    payload = mock_create.call_args.args[2]
    assert payload["cpu_pct"] == 50.0
    assert payload["threshold_breach"] is False, (
        f"expected threshold_breach=False for cpu_pct=50.0 (<=85); got {payload['threshold_breach']!r}"
    )


# -----------------------------------------------------------------------------
# 6. substrate anchor unchanged (ADAPTED)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_substrate_anchor_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """_local_run returns canonical anchors -> _create_monitoring_task with drift_detected=False."""
    async def fake_local_run(cmd: str, timeout: float = 10.0) -> tuple[int, str, str]:
        if "control-postgres-beast" in cmd:
            return 0, infrastructure.ANCHOR_POSTGRES, ""
        if "control-garage-beast" in cmd:
            return 0, infrastructure.ANCHOR_GARAGE, ""
        return -1, "", "unexpected_command"

    monkeypatch.setattr(infrastructure, "_local_run", fake_local_run)
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(infrastructure, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await infrastructure.substrate_anchor_check(stub_db)

    assert mock_create.call_count == 1
    kind = mock_create.call_args.args[1]
    payload = mock_create.call_args.args[2]
    assert kind == "substrate_check"
    assert payload["postgres_match"] is True
    assert payload["garage_match"] is True
    assert payload["drift_detected"] is False, (
        f"expected drift_detected=False on canonical anchors; got {payload['drift_detected']!r}"
    )


# -----------------------------------------------------------------------------
# 7. substrate anchor changed (ADAPTED -- raises drift via payload.drift_detected=True)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_substrate_anchor_changed_raises_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """_local_run returns drifted postgres anchor -> _create_monitoring_task with drift_detected=True."""
    drifted_pg = "2026-05-02T12:00:00.000000000Z"  # different from ANCHOR_POSTGRES

    async def fake_local_run(cmd: str, timeout: float = 10.0) -> tuple[int, str, str]:
        if "control-postgres-beast" in cmd:
            return 0, drifted_pg, ""
        if "control-garage-beast" in cmd:
            return 0, infrastructure.ANCHOR_GARAGE, ""
        return -1, "", "unexpected_command"

    monkeypatch.setattr(infrastructure, "_local_run", fake_local_run)
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(infrastructure, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await infrastructure.substrate_anchor_check(stub_db)

    assert mock_create.call_count == 1
    payload = mock_create.call_args.args[2]
    assert payload["postgres_anchor_observed"] == drifted_pg
    assert payload["postgres_match"] is False
    assert payload["garage_match"] is True
    assert payload["drift_detected"] is True, (
        f"expected drift_detected=True on drifted postgres anchor; got {payload['drift_detected']!r}"
    )
