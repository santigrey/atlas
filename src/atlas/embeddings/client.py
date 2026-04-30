"""Atlas embedding client against TheBeast localhost Ollama.

Uses /api/embed (newer batch endpoint), NOT legacy /api/embeddings.
Default model mxbai-embed-large:latest dim 1024 matches atlas.memory.embedding vector(1024).
LRU in-memory cache (capacity via ATLAS_EMBED_CACHE_SIZE env).
Token telemetry to atlas.events with ns -> ms conversion (reuses atlas.inference.telemetry._ns_to_ms).
No input text captured to atlas.events -- telemetry only.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import structlog

from atlas.db import Database
from atlas.embeddings.cache import EmbeddingCache
from atlas.inference.telemetry import _ns_to_ms

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = os.getenv("ATLAS_EMBED_URL", "http://localhost:11434")
DEFAULT_EMBED_MODEL = "mxbai-embed-large:latest"
EMBED_DIM = 1024
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)


class EmbeddingClient:
    """Async embedding client against TheBeast localhost Ollama /api/embed.

    Single input str -> single vector list[float].
    Batch input list[str] -> list[list[float]].
    Cache hits don't re-call Ollama; full cache hit logs status='cache_full_hit'.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        cache: EmbeddingCache | None = None,
        db: Database | None = None,
    ) -> None:
        self._base_url = base_url or DEFAULT_BASE_URL
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._cache = cache if cache is not None else EmbeddingCache()
        self._db = db
        self._http: httpx.AsyncClient | None = None

    async def open(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "EmbeddingClient":
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    @property
    def cache(self) -> EmbeddingCache:
        return self._cache

    async def embed(
        self,
        text: str | list[str],
        *,
        model: str = DEFAULT_EMBED_MODEL,
    ) -> list[float] | list[list[float]]:
        single_input = isinstance(text, str)
        inputs: list[str] = [text] if single_input else list(text)

        # Cache lookup pass
        results: list[list[float] | None] = []
        cache_hits = 0
        for t in inputs:
            cached = await self._cache.get(model, t)
            if cached is not None:
                cache_hits += 1
            results.append(cached)

        kind = "embed_single" if single_input else "embed_batch"

        # Full cache hit short-circuit
        if all(r is not None for r in results):
            await self._log_full_cache_hit(
                model=model, input_count=len(inputs), cache_hits=cache_hits, kind=kind
            )
            if single_input:
                return results[0]  # type: ignore[return-value]
            return [r for r in results]  # type: ignore[return-value]

        # Compute missing via /api/embed (batch on the missing inputs only)
        missing_indices = [i for i, r in enumerate(results) if r is None]
        missing_texts = [inputs[i] for i in missing_indices]

        await self.open()
        assert self._http is not None
        endpoint = f"{self._base_url}/api/embed"
        body = {"model": model, "input": missing_texts}
        try:
            resp = await self._http.post("/api/embed", json=body)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            await self._log_error(model, len(inputs), endpoint, exc, kind)
            raise

        new_vectors: list[list[float]] = data.get("embeddings", [])
        if len(new_vectors) != len(missing_indices):
            raise RuntimeError(
                f"embed returned {len(new_vectors)} vectors for {len(missing_indices)} inputs"
            )

        # Populate cache + merge into results
        for i, vec in zip(missing_indices, new_vectors):
            await self._cache.put(model, inputs[i], vec)
            results[i] = vec

        await self._log_success(
            data,
            input_count=len(inputs),
            cache_hits=cache_hits,
            endpoint=endpoint,
            kind=kind,
        )

        if single_input:
            return results[0]  # type: ignore[return-value]
        return [r for r in results]  # type: ignore[return-value]

    async def _log_success(
        self,
        data: dict[str, Any],
        *,
        input_count: int,
        cache_hits: int,
        endpoint: str,
        kind: str,
    ) -> None:
        if self._db is None:
            return
        payload = {
            "model": data.get("model", ""),
            "input_count": input_count,
            "prompt_eval_count": data.get("prompt_eval_count"),
            "total_duration_ms": _ns_to_ms(data.get("total_duration")),
            "load_duration_ms": _ns_to_ms(data.get("load_duration")),
            "status": "success",
            "endpoint": endpoint,
            "cache_hits": cache_hits,
        }
        await self._raw_log(kind=kind, payload=payload)

    async def _log_full_cache_hit(
        self,
        *,
        model: str,
        input_count: int,
        cache_hits: int,
        kind: str,
    ) -> None:
        if self._db is None:
            return
        payload = {
            "model": model,
            "input_count": input_count,
            "prompt_eval_count": 0,
            "total_duration_ms": 0.0,
            "load_duration_ms": 0.0,
            "status": "cache_full_hit",
            "endpoint": f"{self._base_url}/api/embed",
            "cache_hits": cache_hits,
        }
        await self._raw_log(kind=kind, payload=payload)

    async def _log_error(
        self,
        model: str,
        input_count: int,
        endpoint: str,
        exc: Exception,
        kind: str,
    ) -> None:
        if self._db is None:
            return
        payload = {
            "model": model,
            "input_count": input_count,
            "prompt_eval_count": None,
            "total_duration_ms": None,
            "load_duration_ms": None,
            "status": "error",
            "endpoint": endpoint,
            "cache_hits": 0,
            "error": str(exc),
        }
        await self._raw_log(kind=kind, payload=payload)

    async def _raw_log(self, *, kind: str, payload: dict[str, Any]) -> None:
        assert self._db is not None
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO atlas.events (source, kind, payload) VALUES (%s, %s, %s::jsonb)",
                    ("atlas.embeddings", kind, json.dumps(payload, default=str)),
                )
                await conn.commit()


def get_embedder(db: Database | None = None) -> EmbeddingClient:
    """Convenience constructor with default base_url + timeout + cache."""
    return EmbeddingClient(db=db)
