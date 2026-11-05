import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from web.conversations.ingest_auth import tenant_key
from web.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Provisiona la clave HMAC de un cliente en un fichero privado, nunca en stdout."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--output", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        slug = options["tenant"]
        if not settings.INGEST_TOKEN or not Tenant.objects.filter(slug=slug).exists():
            raise CommandError("Cliente o secreto raíz no configurados.")
        root = (settings.BASE_DIR / ".runtime-secrets").resolve()
        target = Path(options["output"]).absolute()
        if target.parent.resolve() != root:
            raise CommandError("El destino debe estar directamente en .runtime-secrets.")
        root.mkdir(mode=0o700, exist_ok=True)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise CommandError("El destino existe o no admite una creación segura.") from None
        with os.fdopen(fd, "w") as stream:
            stream.write(tenant_key(settings.INGEST_TOKEN, slug))
        self.stdout.write("Clave de cliente guardada en el fichero privado indicado.")
