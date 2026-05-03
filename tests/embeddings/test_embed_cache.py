"""Cycle 1E smoke: cache hits return same vector + stats increment."""

import pytest

from atlas.embeddings import EmbeddingClient


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_cache_hit_returns_same_vector() -> None:
    async with EmbeddingClient() as client:
        # First call: cache miss
        vec1 = await client.embed("cache hit test text")
        stats_after_first = client.cache.stats()
        assert stats_after_first["misses"] >= 1

        # Second call (same text): cache hit, identical vector returned
        vec2 = await client.embed("cache hit test text")
        stats_after_second = client.cache.stats()
        assert stats_after_second["hits"] >= 1
        assert stats_after_second["hits"] > stats_after_first["hits"]
        assert vec1 == vec2
