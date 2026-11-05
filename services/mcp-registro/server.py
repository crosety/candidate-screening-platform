from mcp.server import MCPServer
from pydantic import BaseModel

server = MCPServer(name="registro-certificados")

REGISTRY: dict[str, dict[str, str | bool | None]] = {
    "4417-CB": {"valid": True, "holder": "Candidato de prueba", "expires": "2028-05-31"},
    "2208-ZX": {"valid": False, "holder": "Candidato de prueba", "expires": "2024-01-31"},
}


class CheckResult(BaseModel):
    number: str
    valid: bool
    holder: str | None = None
    expires: str | None = None


@server.tool(description="Comprueba un certificado de carretillero por su número.")
def check(numero: str) -> CheckResult:
    number = numero.strip().upper()
    data = REGISTRY.get(number, {"valid": False})
    return CheckResult.model_validate({**data, "number": number})


if __name__ == "__main__":
    server.run(transport="streamable-http", host="0.0.0.0", port=8100)
