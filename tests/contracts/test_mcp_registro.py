import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from mcp.client import Client


@pytest.fixture
def registry_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "services/mcp-registro/server.py"
    spec = importlib.util.spec_from_file_location("mcp_registro", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_mcp_registro(registry_module: ModuleType) -> None:
    async with Client(registry_module.server) as client:
        tools = await client.list_tools()
        assert "check" in {tool.name for tool in tools.tools}
        result = await client.call_tool("check", {"numero": "4417-cb"})
        assert result.structured_content == {
            "number": "4417-CB",
            "valid": True,
            "holder": "Candidato de prueba",
            "expires": "2028-05-31",
        }
