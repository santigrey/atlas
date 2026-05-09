-- 0007: create security schema (Mr Robot Phase 0 prereq).
-- Schema-only at this stage; tables defined in Mr Robot Phase 0 build cycle.
-- Pattern copied from 0001_create_atlas_schema.sql (P6 #32 reuse): CREATE SCHEMA IF NOT EXISTS for idempotency.
CREATE SCHEMA IF NOT EXISTS security;
