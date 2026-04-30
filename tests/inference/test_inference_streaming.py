"""Cycle 1D smoke: streaming generate yields chunks; final chunk has done=True."""

import pytest

from atlas.inference import GoliathClient, MODEL_QWEN_72B


@pytest.mark.asyncio
async def test_stream_generate_yields_chunks() -> None:
    async with GoliathClient() as client:
        # warm-up
        await client.generate(
            "Say OK", model=MODEL_QWEN_72B, options={"num_predict": 3}
        )
        # streaming assertion
        chunks = []
        async for chunk in await client.generate(
            "Count to 3",
            model=MODEL_QWEN_72B,
            stream=True,
            options={"num_predict": 10},
        ):
            chunks.append(chunk)
        assert len(chunks) >= 1
        # final chunk has done=True and telemetry
        final = chunks[-1]
        assert final.done is True
        assert final.eval_count is not None and final.eval_count > 0
        assert final.total_duration is not None and final.total_duration > 0
