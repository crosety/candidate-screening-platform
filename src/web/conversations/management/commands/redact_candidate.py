from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser

from screening.domain.errors import Conflict
from web.conversations.privacy import redact_candidate
from web.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Vista previa o retirada auditada local de datos de una candidatura (irreversible)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--application", type=UUID, required=True)
        parser.add_argument(
            "--case-ref",
            required=True,
            help="Referencia opaca del expediente, sin datos personales",
        )
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        case = str(options["case_ref"])
        if not case or len(case) > 100 or not all(c.isalnum() or c in "_-" for c in case):
            raise CommandError("Usa una referencia opaca con letras, cifras, guion o guion bajo.")
        tenant = Tenant.objects.get(slug=options["tenant"])
        try:
            result = redact_candidate(tenant.id, options["application"], case, options["apply"])
        except Conflict as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(str(result))
        self.stdout.write(
            "Retirada local realizada. Proveedores y backups requieren gestión separada."
            if options["apply"]
            else "Solo vista previa; ningún dato ha cambiado."
        )
