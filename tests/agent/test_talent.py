"""Atlas v0.1 Phase 8 -- Tests for atlas.agent.domains.talent.

4 cases per paco_directive_atlas_v0_1_phase8.md section 2.7 (ADAPTED to ground-truth
surface per Path B; Sloan Day 78 evening):

1. test_read_job_search_log_parses_json -- _ssh_run returns valid JSON; _read parses it
2. test_new_url_detection_via_set_diff -- seen_urls minus existing -> _create_monitoring_task only for new
3. test_weekly_digest_aggregates_seven_day_window (HOMELAB) -- INSERT applicant_logged rows; digest captures them
4. test_empty_or_unreadable_log_no_writes -- empty seen_urls OR read failure -> 0 _create_monitoring_task calls

ADAPTATION RATIONALE (preserves directive intent; matches actual implementation):
- talent.py reads JSON via SSH cat (not local CSV/MD with Path.read_text). Schema:
  {\"seen_urls\": [\"url1\", \"url2\", ...]} -- no title/company/applied_date columns.
- New-entry detection is set diff vs atlas.tasks (kind='applicant_logged'); no timestamp filter.
- weekly_digest_compile aggregates 7-day URL count; payload {count, urls, window_days}; no per-company groupby.
- Tests 1, 2, 4 are pure-mock; test 3 uses real DB for digest aggregation with test_run_id URL prefix for zero-leak cleanup.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.agent.domains import talent
from atlas.db import Database


# -----------------------------------------------------------------------------
# 1. _read_job_search_log parses JSON returned via SSH cat
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_job_search_log_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ssh_run returns valid JSON -> _read_job_search_log returns parsed dict."""
    sample = {"seen_urls": ["https://example.com/job/1", "https://example.com/job/2"], "version": 3}
    json_blob = json.dumps(sample)
    mock_ssh = AsyncMock(return_value=(0, json_blob, ""))
    monkeypatch.setattr(talent, "_ssh_run", mock_ssh)

    result = await talent._read_job_search_log()

    assert result == sample, f"expected parsed dict equal to sample; got {result!r}"
    assert mock_ssh.call_count == 1
    ssh_args = mock_ssh.call_args.args
    assert ssh_args[0] == talent.CK_HOST
    assert ssh_args[1] == talent.CK_USER
    assert "cat" in ssh_args[2] and talent.JOB_SEARCH_LOG_PATH in ssh_args[2]

    # Failure path: rc != 0 returns None
    mock_ssh_fail = AsyncMock(return_value=(1, "", "file not found"))
    monkeypatch.setattr(talent, "_ssh_run", mock_ssh_fail)
    assert await talent._read_job_search_log() is None

    # JSON parse failure path returns None
    mock_ssh_garbage = AsyncMock(return_value=(0, "not json", ""))
    monkeypatch.setattr(talent, "_ssh_run", mock_ssh_garbage)
    assert await talent._read_job_search_log() is None


# -----------------------------------------------------------------------------
# 2. new-URL detection via set diff vs _existing_logged_urls
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_url_detection_via_set_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    """seen_urls=[A,B,C] + existing={A} -> _create_monitoring_task called for B + C only."""
    seen = ["https://example.com/job/A", "https://example.com/job/B", "https://example.com/job/C"]
    mock_read = AsyncMock(return_value={"seen_urls": seen})
    monkeypatch.setattr(talent, "_read_job_search_log", mock_read)
    # A is already tracked; B + C are new
    mock_existing = AsyncMock(return_value={"https://example.com/job/A"})
    monkeypatch.setattr(talent, "_existing_logged_urls", mock_existing)
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(talent, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await talent.job_search_log_check(stub_db)

    # Exactly 2 calls (B + C), not 3 (A skipped)
    assert mock_create.call_count == 2, (
        f"expected 2 _create_monitoring_task calls (B + C); got {mock_create.call_count}"
    )
    # Verify each call had kind='applicant_logged' and payload.url matches a new URL
    written_urls = set()
    for call in mock_create.call_args_list:
        kind = call.args[1]
        payload = call.args[2]
        assert kind == "applicant_logged", f"expected kind='applicant_logged'; got {kind!r}"
        assert payload["source"] == "job_search_log"
        written_urls.add(payload["url"])
    assert written_urls == {"https://example.com/job/B", "https://example.com/job/C"}, (
        f"expected only B + C written; got {written_urls}"
    )
    # A must NOT appear
    assert "https://example.com/job/A" not in written_urls


# -----------------------------------------------------------------------------
# 3. weekly_digest aggregates 7-day applicant_logged rows (HOMELAB -- real DB)
# -----------------------------------------------------------------------------

@pytest.mark.homelab
@pytest.mark.asyncio
async def test_weekly_digest_aggregates_seven_day_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INSERT 3 applicant_logged rows with unique URL prefix; verify they appear in digest."""
    test_run_id = uuid.uuid4().hex[:12]
    test_urls = [f"https://test.invalid/{test_run_id}/job/{n}" for n in range(3)]
    mock_create = AsyncMock(return_value="digest-task-uuid")
    monkeypatch.setattr(talent, "_create_monitoring_task", mock_create)

    db = Database()
    await db.open()
    try:
        # INSERT 3 applicant_logged rows tagged with test_run_id URL prefix
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                for url in test_urls:
                    payload = {"kind": "applicant_logged", "url": url, "source": "test_seed"}
                    await cur.execute(
                        "INSERT INTO atlas.tasks (status, payload) "
                        "VALUES ('done', %s::jsonb)",
                        (json.dumps(payload),),
                    )
                await conn.commit()

        await talent.weekly_digest_compile(db)

        # Verify _create_monitoring_task called exactly once with the digest
        assert mock_create.call_count == 1, (
            f"expected 1 digest call; got {mock_create.call_count}"
        )
        kind = mock_create.call_args.args[1]
        payload = mock_create.call_args.args[2]
        assert kind == "weekly_digest_talent"
        assert payload["window_days"] == 7
        assert payload["count"] == len(payload["urls"]), "count must equal len(urls)"
        # All 3 test URLs must be in payload.urls (other production URLs may also be present)
        urls_set = set(payload["urls"])
        for url in test_urls:
            assert url in urls_set, f"test URL {url} missing from digest; got urls={payload['urls']!r}"
    finally:
        # Cleanup: DELETE rows whose payload.url starts with our test_run_id prefix
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM atlas.tasks WHERE payload->>'url' LIKE %s",
                    (f"https://test.invalid/{test_run_id}/%",),
                )
                await conn.commit()
        await db.close()


# -----------------------------------------------------------------------------
# 4. empty seen_urls OR read failure -> 0 _create_monitoring_task calls
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "read_return,case_name",
    [
        ({"seen_urls": []}, "empty_seen_urls"),
        (None, "read_failure"),
        ({"seen_urls": "not_a_list"}, "malformed_seen_urls"),
    ],
)
async def test_empty_or_unreadable_log_no_writes(
    monkeypatch: pytest.MonkeyPatch, read_return, case_name: str
) -> None:
    """Empty list, None, or non-list seen_urls -> early return, 0 _create_monitoring_task calls."""
    mock_read = AsyncMock(return_value=read_return)
    monkeypatch.setattr(talent, "_read_job_search_log", mock_read)
    mock_existing = AsyncMock(return_value=set())
    monkeypatch.setattr(talent, "_existing_logged_urls", mock_existing)
    mock_create = AsyncMock(return_value="task-uuid")
    monkeypatch.setattr(talent, "_create_monitoring_task", mock_create)

    stub_db = MagicMock()
    await talent.job_search_log_check(stub_db)

    assert mock_create.call_count == 0, (
        f"case={case_name}: expected 0 _create_monitoring_task calls; got {mock_create.call_count}"
    )
