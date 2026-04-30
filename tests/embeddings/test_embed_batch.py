"""Cycle 1E smoke: batch input returns list of dim 1024 vectors."""

import pytest

from atlas.embeddings import EMBED_DIM, EmbeddingClient


@pytest.mark.asyncio
async def test_batch_embed_returns_n_vectors() -> None:
    async with EmbeddingClient() as client:
        vecs = await client.embed(["alpha", "beta", "gamma"])
        assert isinstance(vecs, list)
        assert len(vecs) == 3
        assert all(len(v) == EMBED_DIM for v in vecs)
