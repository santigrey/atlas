-- 0002: bootstrap version-tracking table
CREATE TABLE IF NOT EXISTS atlas.schema_version (
  version INT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  description TEXT
);
