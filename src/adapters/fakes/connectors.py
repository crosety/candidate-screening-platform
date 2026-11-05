import asyncio
from copy import deepcopy
from typing import Any

from screening.application.ports import Connector
from screening.domain.profile import Connection


class StaticConnectorRegistry:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, tenant_slug: str, connection: Connection) -> Connector:
        return self

    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, deepcopy(args)))
        return deepcopy(self.responses[operation])


class FailingConnectorRegistry:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def get(self, tenant_slug: str, connection: Connection) -> Connector:
        return self

    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        raise self.exc


class SlowConnectorRegistry:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def get(self, tenant_slug: str, connection: Connection) -> Connector:
        return self

    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(self.seconds)
        return {}
