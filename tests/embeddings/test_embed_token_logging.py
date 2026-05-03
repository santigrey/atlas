"""Cycle 1E smoke: atlas.events row inserted on embed call with db wired."""

import pytest

from atlas.db import Database
from atlas.embeddings import EmbeddingClient


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_embed_inserts_atlas_events_row() -> None:
    db = Database()
    await db.open()
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM atlas.events WHERE source='atlas.embeddings'"
                )
                row = await cur.fetchone()
                assert row is not None
                pre_count = row[0]

        async with EmbeddingClient(db=db) as client:
            await client.embed("telemetry inspection input")

        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM atlas.events WHERE source='atlas.embeddings'"
                )
                row = await cur.fetchone()
                assert row is not None
                post_count = row[0]
                assert post_count == pre_count + 1

                await cur.execute(
                    "SELECT kind, payload FROM atlas.events "
                    "WHERE source='atlas.embeddings' ORDER BY ts DESC LIMIT 1"
                )
                latest = await cur.fetchone()
                assert latest is not None
                kind, payload = latest[0], latest[1]
                assert kind == "embed_single"
                assert payload["model"].startswith("mxbai-embed-large")
                assert payload["input_count"] == 1
                assert payload["prompt_eval_count"] is not None
                assert payload["total_duration_ms"] is not None
                assert payload["status"] == "success"
                assert payload["endpoint"].endswith("/api/embed")
    finally:
        await db.close()
