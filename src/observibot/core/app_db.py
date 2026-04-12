"""Managed connection pool for the monitored application database."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)


class AppDatabasePool:
    """Manages a small, read-only connection pool to the monitored app DB.

    This pool is SEPARATE from the connector pool used for metrics collection.
    It is opt-in — only created if ``chat.enable_app_queries`` is true in config.
    """

    def __init__(
        self,
        dsn: str,
        max_size: int = 3,
        statement_timeout_ms: int = 3000,
    ) -> None:
        self._dsn = dsn
        self._max_size = max_size
        self._statement_timeout_ms = statement_timeout_ms
        self._pool: Any = None

    async def connect(self) -> None:
        """Create the asyncpg connection pool."""
        import asyncpg
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=self._max_size,
            command_timeout=self._statement_timeout_ms / 1000,
        )
        log.info("App database pool connected (max_size=%d)", self._max_size)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection with safety limits enforced."""
        if not self._pool:
            raise RuntimeError("App database pool not connected")
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"SET LOCAL statement_timeout = "
                f"'{self._statement_timeout_ms}'"
            )
            await conn.execute("SET LOCAL lock_timeout = '500'")
            yield conn

    async def execute_sandboxed(
        self, sql: str, max_rows: int = 500,
    ) -> list[dict]:
        """Execute validated SQL and return rows as dicts."""
        async with self.acquire() as conn:
            rows = await conn.fetch(sql)
            result = []
            for r in rows[:max_rows]:
                row_dict = dict(r)
                for k, v in row_dict.items():
                    if not isinstance(v, (str, int, float, bool, type(None))):
                        row_dict[k] = str(v)
                result.append(row_dict)
            return result

    @property
    def is_connected(self) -> bool:
        return self._pool is not None and not getattr(
            self._pool, "_closed", True
        )
