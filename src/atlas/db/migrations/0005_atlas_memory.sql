-- 0005: atlas.memory for working memory + RAG (Cycle 1E consumer)
-- Embedding dim 1024 matches mxbai-embed-large (verified live Day 75)
CREATE TABLE IF NOT EXISTS atlas.memory (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  embedding vector(1024),
  metadata JSONB
);

-- HNSW index deferred to v0.2 hardening (warm-cache acceptable for v0.1 dev usage).
-- Standard btree on kind+ts is sufficient for early Cycle 1+2 needs:
CREATE INDEX IF NOT EXISTS memory_kind_ts_idx ON atlas.memory (kind, ts DESC);
