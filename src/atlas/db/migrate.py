"""Atlas schema migration runner.

Applies SQL migration files in versioned order, idempotently.
Bootstraps atlas schema + schema_version table on first run.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from atlas.db.pool import Database

log = structlog.get_logger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")


async def run_migrations(db: Database) -> int:
    """Apply any pending migrations. Returns count newly applied this call."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        return 0

    # Bootstrap: ensure atlas schema + schema_version table exist (idempotent)
    bootstrap_files = [
        MIGRATIONS_DIR / "0001_create_atlas_schema.sql",
        MIGRATIONS_DIR / "0002_atlas_schema_version.sql",
    ]
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            for bf in bootstrap_files:
                if bf.exists():
                    await cur.execute(bf.read_text())
            await conn.commit()

    # Query already-applied versions
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT version FROM atlas.schema_version")
            applied: set[int] = {row[0] for row in await cur.fetchall()}

    count_applied = 0
    for f in files:
        m = MIGRATION_PATTERN.match(f.name)
        if not m:
            continue
        version = int(m.group(1))
        description = m.group(2)
        if version in applied:
            continue
        sql = f.read_text()
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                await cur.execute(
                    "INSERT INTO atlas.schema_version (version, description) "
                    "VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                    (version, description),
                )
                await conn.commit()
        log.info("migration_applied", version=version, name=description)
        count_applied += 1

    return count_applied
