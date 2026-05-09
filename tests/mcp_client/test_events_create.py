"""Integration test for atlas_events_create -- DB-direct via create_event helper.

Path B.4 (pre-authorized paco_directive_alexandra_mc2_beta_D1.md §5):
tests/mcp_client/ lacks test_events_search.py; no conftest.py / db fixture
available in the tests tree. Mirrors tests/db/test_db_smoke.py inline
Database() pattern (verified live: db.open() / db.connection() / db.close()).
Test logic preserved verbatim from directive §3 Step E.
"""

import pytest

from atlas.db import Database
from atlas.mcp_server.events import create_event
from atlas.mcp_server.inputs import EventsCreateInput


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_create_event_inserts_and_returns_id() -> None:
    db = Database()
    await db.open()
    try:
        params = EventsCreateInput(
            source="atlas.test.d1",
            kind="d1_smoke",
            payload={"test_run": "MC2.β.D1", "marker": "directive_validation"},
        )
        result = await create_event(params, db)
        assert "id" in result
        assert "ts" in result
        assert isinstance(result["id"], int)

        # Verify row landed in atlas.events
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT source, kind, payload FROM atlas.events WHERE id = %s",
                    (result["id"],),
                )
                row = await cur.fetchone()
        assert row[0] == "atlas.test.d1"
        assert row[1] == "d1_smoke"
        assert row[2] == {"test_run": "MC2.β.D1", "marker": "directive_validation"}
    finally:
        await db.close()
