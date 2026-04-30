-- 0001: create atlas schema + ensure pgvector available
CREATE SCHEMA IF NOT EXISTS atlas;
CREATE EXTENSION IF NOT EXISTS vector;
