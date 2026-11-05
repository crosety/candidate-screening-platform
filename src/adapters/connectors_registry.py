from django.conf import settings

from connectors.mcp import McpConnector
from screening.application.ports import Clock, Connector
from screening.domain.errors import NotFound
from screening.domain.profile import Connection
from web.simulated.connectors import SimAtsConnector, SimCalendarConnector, SimSitesConnector


class DefaultConnectorRegistry:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def get(self, tenant_slug: str, connection: Connection) -> Connector:
        if connection.transport == "mcp":
            url, tool = connection.settings.get("url"), connection.settings.get("tool")
            if not url or not tool:
                raise NotFound("Falta la URL o la herramienta del conector MCP")
            if url not in settings.MCP_ALLOWED_URLS:
                raise NotFound("URL MCP no autorizada por el operador de la plataforma")
            # Legacy seeds contain a placeholder, not a credential, for this one
            # fixed synthetic server. Expose that limitation instead of claiming auth.
            local_placeholder = (
                url
                in {
                    "http://mcp-registro:8100/mcp",
                    "http://localhost:8100/mcp",
                    "http://127.0.0.1:8100/mcp",
                }
                and connection.secret_ref == f"secret://{tenant_slug}/registro"
            )
            if connection.secret_ref and not local_placeholder:
                raise NotFound("Este adaptador MCP no admite autenticación; no ignora secretos")
            return McpConnector(url, tool, unauthenticated_local=local_placeholder)
        drivers = {
            "sim_sites": SimSitesConnector,
            "sim_ats": SimAtsConnector,
            "sim_calendar": SimCalendarConnector,
        }
        driver = drivers.get(connection.settings.get("driver", ""))
        if connection.transport != "native" or driver is None:
            raise NotFound("Conector no disponible")
        return driver(tenant_slug, connection, self.clock)
