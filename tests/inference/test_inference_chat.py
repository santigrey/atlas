"""Cycle 1D smoke: sync chat against Goliath qwen2.5:72b."""

import pytest

from atlas.inference import ChatMessage, GoliathClient, MODEL_QWEN_72B


@pytest.mark.asyncio
async def test_sync_chat_qwen_72b() -> None:
    async with GoliathClient() as client:
        # warm-up
        await client.chat(
            [ChatMessage(role="user", content="Say OK")],
            model=MODEL_QWEN_72B,
            options={"num_predict": 3},
        )
        # assertion
        resp = await client.chat(
            [ChatMessage(role="user", content="Reply with: OK")],
            model=MODEL_QWEN_72B,
            options={"num_predict": 5},
        )
        assert resp.message.role == "assistant"
        assert len(resp.message.content) > 0
        assert resp.eval_count is not None and resp.eval_count > 0
