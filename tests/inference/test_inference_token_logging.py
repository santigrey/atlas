"""Cycle 1D smoke: atlas.events row inserted with correct payload after inference."""

import pytest

from atlas.db import Database
from atlas.inference import GoliathClient, MODEL_QWEN_72B


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_token_logging_inserts_event() -> None:
    db = Database()
    await db.open()
    try:
        # capture row count before
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM atlas.events WHERE source='atlas.inference'"
                )
                row = await cur.fetchone()
                assert row is not None
                pre_count = row[0]

        # do an inference call with db wired in
        async with GoliathClient(db=db) as client:
            await client.generate(
                "Say OK", model=MODEL_QWEN_72B, options={"num_predict": 3}
            )

        # verify row count went up by 1
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM atlas.events WHERE source='atlas.inference'"
                )
                row = await cur.fetchone()
                assert row is not None
                post_count = row[0]
                assert post_count == pre_count + 1

                # latest row has expected structure
                await cur.execute(
                    "SELECT kind, payload FROM atlas.events "
                    "WHERE source='atlas.inference' ORDER BY ts DESC LIMIT 1"
                )
                latest = await cur.fetchone()
                assert latest is not None
                kind, payload = latest[0], latest[1]
                assert kind == "generate"
                assert payload["model"] == MODEL_QWEN_72B
                assert "prompt_eval_count" in payload
                assert "eval_count" in payload
                assert "total_duration_ms" in payload
                assert payload["status"] == "success"
    finally:
        await db.close()
