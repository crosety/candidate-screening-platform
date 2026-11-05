"""Read-only readiness check; never prints credentials, contacts or message bodies."""

from pathlib import Path
from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from adapters.channels.twilio import TwilioConfig
from web.conversations.models import TwilioOutbound
from web.tenancy.context import tenant_context


class Command(BaseCommand):
    help = "Comprueba configuración y cola de Twilio sin enviar mensajes ni llamar"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--remote", action="store_true", help="GET de estado/tipo de cuenta")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            path = Path(settings.TWILIO_CONFIG_FILE)
            config = TwilioConfig.model_validate_json(path.read_text())
        except (OSError, ValueError):
            raise CommandError(
                "Falta configuración privada válida de Twilio. Sin envíos."
            ) from None
        if path.stat().st_mode & 0o077:
            raise CommandError("El archivo de Twilio debe ser privado (chmod 600).")
        self.stdout.write(f"Envíos habilitados y vigentes: {config.active}")
        if config.expires_at:
            self.stdout.write(f"Caducidad de la autorización: {config.expires_at.isoformat()}")
        self.stdout.write(f"Participantes autorizados: {len(config.participants)}")
        self.stdout.write(f"Máximo de intentos de salida/24 h: {config.max_outbound_per_day}")
        with tenant_context(config.tenant_id):
            for state in [
                "pending",
                "dispatching",
                "unknown",
                "blocked",
                "failed",
                "sent",
                "delivered",
                "read",
            ]:
                self.stdout.write(f"{state}: {TwilioOutbound.objects.filter(state=state).count()}")
        if not options["remote"]:
            return
        try:
            with httpx.Client(
                base_url="https://api.twilio.com",
                timeout=15,
                follow_redirects=False,
                auth=(config.account_sid, config.auth_token.get_secret_value()),
            ) as client:
                response = client.get(f"/2010-04-01/Accounts/{config.account_sid}.json")
                response.raise_for_status()
                data = response.json()
                account_type, status = data.get("type"), data.get("status")
                if account_type not in {"Trial", "Full"} or status not in {
                    "active",
                    "suspended",
                    "closed",
                }:
                    raise ValueError("Respuesta no reconocida")
                self.stdout.write(f"Cuenta verificada: tipo={account_type}, estado={status}")
                if account_type == "Trial":
                    self.stdout.write(
                        "Telefonía conversacional no validada: revisar bloqueo de Stream en trial."
                    )
        except (httpx.HTTPError, ValueError):
            raise CommandError(
                "No se pudo verificar la cuenta Twilio; no se ha enviado nada."
            ) from None
