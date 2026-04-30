-- 0004: atlas.events for cross-module event log
CREATE TABLE IF NOT EXISTS atlas.events (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload JSONB
);

CREATE INDEX IF NOT EXISTS events_ts_idx ON atlas.events (ts DESC);
CREATE INDEX IF NOT EXISTS events_source_kind_idx ON atlas.events (source, kind);
