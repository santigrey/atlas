"""Cycle 1B smoke: run migrations, verify atlas schema + 5 tables (vendors added Phase 5)."""

import pytest

from atlas.db import Database, run_migrations


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_migrations_idempotent() -> None:
    db = Database()
    await db.open()
    try:
        # First run applies pending migrations
        first = await run_migrations(db)
        assert first >= 0
        # Second run is no-op (idempotent)
        second = await run_migrations(db)
        assert second == 0
        # Verify expected tables exist in atlas schema
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='atlas' ORDER BY tablename"
                )
                tables = [row[0] for row in await cur.fetchall()]
                assert tables == ["events", "memory", "schema_version", "tasks", "vendors"]
    finally:
        await db.close()
