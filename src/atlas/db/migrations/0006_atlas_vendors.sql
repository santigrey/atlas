-- 0006: atlas.vendors for Phase 5 Domain 3 vendor & admin tracking
-- Per spec lines 357-385 (tasks/atlas_v0_1_agent_loop.md, amended at HEAD 39ffe07).
-- Pattern copied from 0005_atlas_memory.sql (P6 #32 reuse): CREATE TABLE IF NOT EXISTS
-- + CREATE INDEX IF NOT EXISTS for idempotency. INSERT ON CONFLICT DO NOTHING for seed.
CREATE TABLE IF NOT EXISTS atlas.vendors (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    plan_tier           TEXT,
    billing_cycle       TEXT CHECK (billing_cycle IN ('monthly', 'annual', 'one-time', 'unknown')),
    renewal_date        DATE,
    monthly_cost_usd    NUMERIC(10,2),
    primary_contact_url TEXT,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'cancelled', 'free')),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_atlas_vendors_renewal ON atlas.vendors (renewal_date) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_atlas_vendors_status  ON atlas.vendors (status);

-- Seed 7 active vendors per Charter 5 + Atlas SOP v1.0 Section 2.3.
-- renewal_date left NULL: Sloan fills via dashboard or direct UPDATE post-ship.
-- ON CONFLICT belt-and-braces (runner skips already-applied versions but seed is
-- safe to re-run if migration is rolled back + re-applied).
INSERT INTO atlas.vendors (name, plan_tier, billing_cycle, primary_contact_url, status) VALUES
    ('Anthropic',   'unknown', 'monthly',  'https://console.anthropic.com',       'active'),
    ('GitHub',      'unknown', 'monthly',  'https://github.com/settings/billing', 'active'),
    ('Twilio',      'unknown', 'monthly',  'https://console.twilio.com',          'active'),
    ('ElevenLabs',  'unknown', 'monthly',  'https://elevenlabs.io/app',           'active'),
    ('Per Scholas', 'program', 'one-time', 'https://perscholas.org',              'active'),
    ('Google',      'unknown', 'monthly',  'https://myaccount.google.com',        'active'),
    ('Tailscale',   'unknown', 'monthly',  'https://login.tailscale.com',         'active')
ON CONFLICT (name) DO NOTHING;
