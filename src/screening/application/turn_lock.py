import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from uuid import UUID
from weakref import WeakValueDictionary


class TurnLock(Protocol):
    def hold(self, tenant_id: UUID, session_id: UUID) -> AbstractAsyncContextManager[None]: ...


class InProcessTurnLock:
    def __init__(self) -> None:
        self.locks: WeakValueDictionary[tuple[UUID, UUID], asyncio.Lock] = WeakValueDictionary()

    @asynccontextmanager
    async def hold(self, tenant_id: UUID, session_id: UUID) -> AsyncIterator[None]:
        async with self.locks.setdefault((tenant_id, session_id), asyncio.Lock()):
            yield
