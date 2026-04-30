"""Cycle 1B smoke: connect via .pgpass, run SELECT 1."""

import pytest

from atlas.db import Database


@pytest.mark.asyncio
async def test_connect_and_select_one() -> None:
    db = Database()
    await db.open()
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1, current_database()")
                row = await cur.fetchone()
                assert row == (1, "controlplane")
    finally:
        await db.close()
