"""Atlas Postgres connection pool.

Reads from public.* and agent_os.* are read-only by code convention.
Writes only to atlas.*.
DSN via ATLAS_PG_DSN env (default: connects as admin to controlplane DB on localhost; libpq picks up .pgpass for password).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg_pool import AsyncConnectionPool


# Default DSN: explicit user=admin so libpq matches .pgpass entry
# (without explicit user, libpq defaults to OS user which has no PG role).
DEFAULT_DSN = "postgresql://admin@localhost/controlplane"


def get_dsn() -> str:
    """Return DSN from env, defaulting to admin@localhost/controlplane."""
    return os.getenv("ATLAS_PG_DSN", DEFAULT_DSN)


class Database:
    """Async pool wrapper for Atlas Postgres access."""

    def __init__(self, dsn: str | None = None, min_size: int = 2, max_size: int = 10) -> None:
        self._dsn = dsn or get_dsn()
        self._pool: AsyncConnectionPool | None = None
        self._min_size = min_size
        self._max_size = max_size

    async def open(self) -> None:
        if self._pool is None:
            self._pool = AsyncConnectionPool(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                open=False,
            )
            await self._pool.open()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator:
        if self._pool is None:
            await self.open()
        assert self._pool is not None
        async with self._pool.connection() as conn:
            yield conn
