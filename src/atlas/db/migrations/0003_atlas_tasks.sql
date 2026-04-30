-- 0003: atlas.tasks for task dispatch (Cycle 1H consumer)
CREATE TABLE IF NOT EXISTS atlas.tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status TEXT NOT NULL CHECK (status IN ('pending','running','done','failed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  owner TEXT,
  payload JSONB,
  result JSONB
);

CREATE INDEX IF NOT EXISTS tasks_status_created_idx ON atlas.tasks (status, created_at);
