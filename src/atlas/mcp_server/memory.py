"""atlas_memory_query + atlas_memory_upsert implementations.

Server-side embedding generation via atlas.embeddings.EmbeddingClient.
Vector parameterization uses string-format `[v1,v2,...]` + `%s::vector` cast
(no pgvector-python adapter installed).
"""

from __future__ import annotations

import json
from typing import Any

from atlas.db import Database
from atlas.embeddings import EmbeddingClient, get_embedder
from atlas.mcp_server.inputs import MemoryQueryInput, MemoryUpsertInput

# Module-level lazy embedder (one HTTP client per process; opens on first use)
_embedder: EmbeddingClient | None = None


def _get_embedder() -> EmbeddingClient:
    global _embedder
    if _embedder is None:
        _embedder = get_embedder()
    return _embedder


def _vector_literal(embedding: list[float]) -> str:
    """Format a Python list[float] as a Postgres vector literal: '[v1,v2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


async def query_memory(
    params: MemoryQueryInput, db: Database
) -> list[dict[str, Any]]:
    """Server embeds query_text and finds nearest neighbors in atlas.memory.

    Returns list of dicts {id, ts, kind, content, metadata, distance}, ordered
    by distance ASC (cosine via `<->` operator on vector(1024) column).
    """
    embedder = _get_embedder()
    embedding = await embedder.embed(params.query_text)
    # embed(str) returns list[float] for single input
    assert isinstance(embedding, list) and (
        len(embedding) == 0 or isinstance(embedding[0], float)
    ), "embed_single must return list[float]"
    vec_lit = _vector_literal(embedding)  # type: ignore[arg-type]

    sql_parts: list[str] = [
        "SELECT id, ts, kind, content, metadata, "
        "(embedding <-> %s::vector) AS distance "
        "FROM atlas.memory WHERE embedding IS NOT NULL"
    ]
    args: list[Any] = [vec_lit]

    if params.kind is not None:
        sql_parts.append("AND kind = %s")
        args.append(params.kind)

    sql_parts.append("ORDER BY embedding <-> %s::vector ASC LIMIT %s")
    args.append(vec_lit)
    args.append(params.top_k)

    sql = " ".join(sql_parts)

    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            rows = await cur.fetchall()

    return [
        {
            "id": r[0],
            "ts": r[1],
            "kind": r[2],
            "content": r[3],
            "metadata": r[4],
            "distance": float(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ]


async def upsert_memory(
    params: MemoryUpsertInput, db: Database
) -> dict[str, Any]:
    """Server embeds content and INSERTs one row into atlas.memory.

    Returns dict {id, ts, kind} -- NOT content/embedding/metadata to avoid
    round-tripping large data in the response.
    """
    embedder = _get_embedder()
    embedding = await embedder.embed(params.content)
    assert isinstance(embedding, list) and (
        len(embedding) == 0 or isinstance(embedding[0], float)
    ), "embed_single must return list[float]"
    vec_lit = _vector_literal(embedding)  # type: ignore[arg-type]

    metadata_json: Any = None
    if params.metadata is not None:
        metadata_json = json.dumps(params.metadata, default=str)

    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO atlas.memory (kind, content, embedding, metadata) "
                "VALUES (%s, %s, %s::vector, %s::jsonb) "
                "RETURNING id, ts",
                (params.kind, params.content, vec_lit, metadata_json),
            )
            row = await cur.fetchone()
            await conn.commit()

    if row is None:
        raise RuntimeError("INSERT INTO atlas.memory returned no row")
    return {"id": row[0], "ts": row[1], "kind": params.kind}
