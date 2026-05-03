"""Cycle 1B smoke: verify read from public.* (replicated state via B2b) works."""

import pytest

from atlas.db import Database


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_read_public_agent_tasks() -> None:
    db = Database()
    await db.open()
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) FROM public.agent_tasks")
                row = await cur.fetchone()
                assert row is not None
                assert row[0] >= 0
    finally:
        await db.close()
