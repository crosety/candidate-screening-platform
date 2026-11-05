import asyncio
from collections.abc import Callable
from typing import Any

from mcp.client import Client


class McpConnector:
    def __init__(
        self,
        url: str,
        tool: str,
        client_factory: Callable[[Any], Client] = Client,
        *,
        unauthenticated_local: bool = False,
    ) -> None:
        self._url, self._tool, self._client_factory = url, tool, client_factory
        self.unauthenticated_local = unauthenticated_local

    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        async with asyncio.timeout(10):
            return await self._call(operation, args)

    async def _call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        if operation == "status":
            try:
                async with self._client_factory(self._url) as client:
                    tools = (await client.list_tools()).tools
                names = [tool.name for tool in tools]
                return {
                    "summary": f"Servidor MCP con herramientas: {', '.join(names)}",
                    "warnings": (
                        [
                            {
                                "code": "mcp_local_no_auth",
                                "text": "Registro sintético local sin autenticación; "
                                "la referencia heredada no es una credencial.",
                            }
                        ]
                        if self.unauthenticated_local
                        else []
                    )
                    if self._tool in names
                    else [
                        {
                            "code": "mcp_tool_missing",
                            "text": f"El servidor no tiene la herramienta «{self._tool}».",
                        }
                    ],
                }
            except Exception:
                return {
                    "summary": "Sin conexión",
                    "warnings": [
                        {
                            "code": "mcp_unreachable",
                            "text": "No se puede conectar con el servidor MCP.",
                        }
                    ],
                }
        if operation == "verify_credential":
            async with self._client_factory(self._url) as client:
                result = await client.call_tool(self._tool, {"numero": args["number"]})
            if result.is_error or result.structured_content is None:
                raise RuntimeError("Respuesta MCP no válida")
            data = result.structured_content
            return {
                "status": "verified" if data["valid"] else "invalid",
                "expires": data.get("expires"),
            }
        if operation == "inspect":
            async with self._client_factory(self._url) as client:
                tools = (await client.list_tools()).tools
            return {
                "structure": [
                    f"{tool.name}({', '.join(tool.input_schema.get('properties', {}))}) "
                    f"→ {', '.join((tool.output_schema or {}).get('properties', {}))}"
                    for tool in tools
                ]
            }
        raise ValueError(f"Operación desconocida: {operation}")
