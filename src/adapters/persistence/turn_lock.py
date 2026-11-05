import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg


class PostgresTurnLock:
    def __init__(self, connection_info: str) -> None:
        self.connection_info = connection_info

    @asynccontextmanager
    async def hold(self, tenant_id: UUID, session_id: UUID) -> AsyncIterator[None]:
        digest = hashlib.sha256(f"{tenant_id}:{session_id}".encode()).digest()[:8]
        key = int.from_bytes(digest, "big", signed=True)
        connection = await psycopg.AsyncConnection.connect(
            self.connection_info, autocommit=True, connect_timeout=5
        )
        try:
            await connection.execute("SET lock_timeout = '30s'")
            await connection.execute("SELECT pg_advisory_lock(%s)", (key,))
            yield
        finally:
            # A closed connection releases its session locks even on cancellation.
            await connection.close()
