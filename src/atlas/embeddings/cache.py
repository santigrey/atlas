"""Atlas embedding LRU cache.

Key: SHA-256 hex of (model || \\x00 || text).
In-memory only; capacity configurable via ATLAS_EMBED_CACHE_SIZE env (default 4096).
Async-safe via asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections import OrderedDict
from typing import Any

DEFAULT_CACHE_SIZE = 4096


def _key(model: str, text: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


class EmbeddingCache:
    """Async-safe LRU for embedding vectors.

    On get: missing key -> miss++ + return None; present -> hit++ + LRU touch + return vector.
    On put: missing key -> insert + evict LRU if at capacity; present -> LRU touch only.
    """

    def __init__(self, capacity: int | None = None) -> None:
        if capacity is None:
            capacity = int(os.getenv("ATLAS_EMBED_CACHE_SIZE", str(DEFAULT_CACHE_SIZE)))
        if capacity < 1:
            raise ValueError(f"cache capacity must be positive, got {capacity}")
        self._capacity = capacity
        self._data: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, model: str, text: str) -> list[float] | None:
        k = _key(model, text)
        async with self._lock:
            vec = self._data.get(k)
            if vec is None:
                self._misses += 1
                return None
            self._data.move_to_end(k)
            self._hits += 1
            return vec

    async def put(self, model: str, text: str, vec: list[float]) -> None:
        k = _key(model, text)
        async with self._lock:
            if k in self._data:
                self._data.move_to_end(k)
                return
            self._data[k] = vec
            if len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def stats(self) -> dict[str, Any]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._data),
            "capacity": self._capacity,
        }
