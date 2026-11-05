import pytest
from mcp.client import Client

from connectors.mcp import McpConnector
from tests.contracts.test_mcp_registro import registry_module as registry_module

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "number,status,expires",
    [
        ("4417-CB", "verified", "2028-05-31"),
        ("2208-ZX", "invalid", "2024-01-31"),
        ("9999-XX", "invalid", None),
    ],
)
async def test_verify(registry_module, number: str, status: str, expires: str | None) -> None:
    connector = McpConnector("unused", "check", lambda _: Client(registry_module.server))
    assert await connector.call("verify_credential", {"number": number}) == {
        "status": status,
        "expires": expires,
    }


async def test_discovery_and_missing_tool(registry_module) -> None:
    connector = McpConnector("unused", "check", lambda _: Client(registry_module.server))
    assert (await connector.call("status", {}))["warnings"] == []
    assert "check(numero)" in (await connector.call("inspect", {}))["structure"][0]
    missing = McpConnector("unused", "otra", lambda _: Client(registry_module.server))
    assert (await missing.call("status", {}))["warnings"][0]["code"] == "mcp_tool_missing"
