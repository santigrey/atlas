"""Atlas Goliath Ollama inference client.

Goliath LAN endpoint: http://192.168.1.20:11434 (Beast Tailscale enrollment is v0.2 P5).
Library-default discipline: explicit timeouts, base_url, raise_for_status, json= kwarg.
NDJSON streaming via httpx aiter_lines (NOT SSE).
Durations in atlas.events stored as MILLISECONDS (ns -> ms via build_telemetry).
No prompt/response content captured to atlas.events -- telemetry only.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx
import structlog

from atlas.db import Database
from atlas.inference.models import (
    ChatChunk,
    ChatMessage,
    ChatResponse,
    GenerateChunk,
    GenerateResponse,
)
from atlas.inference.telemetry import build_telemetry, log_inference_event

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = os.getenv("ATLAS_GOLIATH_URL", "http://192.168.1.20:11434")

MODEL_QWEN_72B = "qwen2.5:72b"
MODEL_DEEPSEEK_70B = "deepseek-r1:70b"
MODEL_LLAMA_70B = "llama3.1:70b"

DEFAULT_MODEL_CHAIN = [MODEL_QWEN_72B, MODEL_DEEPSEEK_70B, MODEL_LLAMA_70B]
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)


class GoliathClient:
    """Async Ollama client against Goliath LAN endpoint.

    Sync + streaming for /api/generate and /api/chat.
    Token telemetry logged to atlas.events when db is provided.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        db: Database | None = None,
    ) -> None:
        self._base_url = base_url or DEFAULT_BASE_URL
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._db = db
        self._http: httpx.AsyncClient | None = None

    async def open(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "GoliathClient":
        await self.open()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def generate(
        self,
        prompt: str,
        *,
        model: str = MODEL_QWEN_72B,
        stream: bool = False,
        options: dict[str, Any] | None = None,
    ) -> GenerateResponse | AsyncIterator[GenerateChunk]:
        body: dict[str, Any] = {"model": model, "prompt": prompt, "stream": stream}
        if options:
            body["options"] = options
        if stream:
            return self._stream_generate(body)
        return await self._sync_generate(body)

    async def _sync_generate(self, body: dict[str, Any]) -> GenerateResponse:
        await self.open()
        assert self._http is not None
        endpoint = f"{self._base_url}/api/generate"
        try:
            resp = await self._http.post("/api/generate", json=body)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            await self._log_error("generate", body["model"], endpoint, exc)
            raise
        await self._log_success("generate", data, endpoint)
        return GenerateResponse(**data)

    async def _stream_generate(self, body: dict[str, Any]) -> AsyncIterator[GenerateChunk]:
        await self.open()
        assert self._http is not None
        endpoint = f"{self._base_url}/api/generate"
        last_payload: dict[str, Any] | None = None
        try:
            async with self._http.stream("POST", "/api/generate", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk_data = json.loads(line)
                    last_payload = chunk_data
                    yield GenerateChunk(**chunk_data)
        except Exception as exc:
            await self._log_error("stream_generate", body["model"], endpoint, exc)
            raise
        if last_payload is not None and last_payload.get("done"):
            await self._log_success("stream_generate", last_payload, endpoint)

    async def chat(
        self,
        messages: list[ChatMessage] | list[dict[str, str]],
        *,
        model: str = MODEL_QWEN_72B,
        stream: bool = False,
        options: dict[str, Any] | None = None,
    ) -> ChatResponse | AsyncIterator[ChatChunk]:
        msgs = [m.model_dump() if isinstance(m, ChatMessage) else m for m in messages]
        body: dict[str, Any] = {"model": model, "messages": msgs, "stream": stream}
        if options:
            body["options"] = options
        if stream:
            return self._stream_chat(body)
        return await self._sync_chat(body)

    async def _sync_chat(self, body: dict[str, Any]) -> ChatResponse:
        await self.open()
        assert self._http is not None
        endpoint = f"{self._base_url}/api/chat"
        try:
            resp = await self._http.post("/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            await self._log_error("chat", body["model"], endpoint, exc)
            raise
        await self._log_success("chat", data, endpoint)
        return ChatResponse(**data)

    async def _stream_chat(self, body: dict[str, Any]) -> AsyncIterator[ChatChunk]:
        await self.open()
        assert self._http is not None
        endpoint = f"{self._base_url}/api/chat"
        last_payload: dict[str, Any] | None = None
        try:
            async with self._http.stream("POST", "/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk_data = json.loads(line)
                    last_payload = chunk_data
                    yield ChatChunk(**chunk_data)
        except Exception as exc:
            await self._log_error("stream_chat", body["model"], endpoint, exc)
            raise
        if last_payload is not None and last_payload.get("done"):
            await self._log_success("stream_chat", last_payload, endpoint)

    async def _log_success(self, kind: str, data: dict[str, Any], endpoint: str) -> None:
        if self._db is None:
            return
        telem = build_telemetry(
            data, fallback_chain=[data.get("model", "")], endpoint=endpoint
        )
        try:
            await log_inference_event(self._db, kind=kind, telemetry=telem)
        except Exception:
            log.exception("telemetry_log_failed")

    async def _log_error(
        self, kind: str, model: str, endpoint: str, exc: Exception
    ) -> None:
        if self._db is None:
            return
        telem = build_telemetry(
            {"model": model},
            fallback_chain=[model],
            endpoint=endpoint,
            status="error",
            error=str(exc),
        )
        try:
            await log_inference_event(self._db, kind=kind, telemetry=telem)
        except Exception:
            log.exception("telemetry_log_failed")


def get_client(db: Database | None = None) -> GoliathClient:
    """Convenience constructor with default base_url + timeout."""
    return GoliathClient(db=db)
