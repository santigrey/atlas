-- 0008: create security.findings table (Mr Robot Phase 0 build).
-- Schema spec from docs/mr_robot_sop_v1_0.md §4.
-- Pattern copied from 0001 + 0007 (P6 #32 reuse): IF NOT EXISTS for idempotency.
CREATE TABLE IF NOT EXISTS security.findings (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    target      TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence    JSONB NOT NULL DEFAULT '{}'::jsonb,
    status      TEXT NOT NULL DEFAULT 'open',
    assigned_to TEXT NOT NULL DEFAULT 'paco',
    CONSTRAINT findings_category_check CHECK (category IN ('pentest','audit','drift','cve')),
    CONSTRAINT findings_severity_check CHECK (severity IN ('low','medium','high','critical','info')),
    CONSTRAINT findings_status_check CHECK (status IN ('open','in_progress','resolved','wontfix'))
);

CREATE INDEX IF NOT EXISTS findings_ts_idx ON security.findings (ts DESC);
CREATE INDEX IF NOT EXISTS findings_severity_status_idx ON security.findings (severity, status);
CREATE INDEX IF NOT EXISTS findings_assigned_to_idx ON security.findings (assigned_to);
