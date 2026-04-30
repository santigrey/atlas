"""Cycle 1D smoke: sync generate against Goliath qwen2.5:72b."""

import pytest

from atlas.inference import GoliathClient, MODEL_QWEN_72B


@pytest.mark.asyncio
async def test_sync_generate_qwen_72b() -> None:
    async with GoliathClient() as client:
        # warm-up call to avoid cold-start variance in assertions
        await client.generate(
            "Say OK", model=MODEL_QWEN_72B, options={"num_predict": 3}
        )
        # actual assertion call (model now warm)
        resp = await client.generate(
            "Reply with the single word: OK",
            model=MODEL_QWEN_72B,
            options={"num_predict": 5},
        )
        assert resp.model == MODEL_QWEN_72B
        assert resp.done is True
        assert len(resp.response) > 0
        assert resp.eval_count is not None and resp.eval_count > 0
        assert resp.total_duration is not None and resp.total_duration > 0
