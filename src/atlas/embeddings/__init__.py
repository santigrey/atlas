"""Atlas embedding service against TheBeast localhost Ollama.

Default endpoint: http://localhost:11434/api/embed (NOT legacy /api/embeddings).
Default model: mxbai-embed-large:latest, dim 1024.
Matches atlas.memory.embedding vector(1024) schema from Cycle 1B.

LRU in-memory cache (capacity via ATLAS_EMBED_CACHE_SIZE env, default 4096).
Token telemetry to atlas.events (source='atlas.embeddings', kinds: embed_single | embed_batch).
Durations stored in MILLISECONDS (converted from Ollama ns).
No input text content captured to atlas.events -- telemetry only.
"""

from atlas.embeddings.cache import DEFAULT_CACHE_SIZE, EmbeddingCache
from atlas.embeddings.client import (
    DEFAULT_BASE_URL,
    DEFAULT_EMBED_MODEL,
    DEFAULT_TIMEOUT,
    EMBED_DIM,
    EmbeddingClient,
    get_embedder,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CACHE_SIZE",
    "DEFAULT_EMBED_MODEL",
    "DEFAULT_TIMEOUT",
    "EMBED_DIM",
    "EmbeddingCache",
    "EmbeddingClient",
    "get_embedder",
]
