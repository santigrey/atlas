"""Cycle 1E smoke: single embed returns dim 1024 vector."""

import pytest

from atlas.embeddings import EMBED_DIM, EmbeddingClient


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_single_embed_returns_dim_1024() -> None:
    async with EmbeddingClient() as client:
        vec = await client.embed("hello world")
        assert isinstance(vec, list)
        assert len(vec) == EMBED_DIM
        assert all(isinstance(x, float) for x in vec[:5])
