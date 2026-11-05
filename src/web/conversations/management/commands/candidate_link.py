"""Explicit local operator recovery/revocation; never emails or sends the link."""

from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from web.conversations.links import derived_token, digest_token, link_expiry
from web.conversations.models import Application, CandidateLink, Event
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Revoca o renueva un enlace. La renovación no imprime el token; se consulta en ATS."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--application", required=True, type=UUID)
        parser.add_argument("--action", choices=["revoke", "rotate"], required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["confirm"]:
            raise CommandError("Requiere --confirm; no se ha modificado ningún enlace.")
        tenant = Tenant.objects.get(slug=options["tenant"])
        with transaction.atomic(), tenant_context(tenant.id):
            if not Application.objects.filter(
                tenant=tenant, pk=options["application"], erased_at__isnull=True
            ).exists():
                raise CommandError("Candidatura no encontrada o retirada.")
            link = CandidateLink.objects.select_for_update().get(
                tenant=tenant, application_id=options["application"]
            )
            link.revoked_at = timezone.now()
            if options["action"] == "rotate":
                link.created_at = timezone.now()
                link.expires_at = link_expiry()
                link.revoked_at = None
                link.token = digest_token(derived_token(link))
            link.save(update_fields=["created_at", "expires_at", "revoked_at", "token"])
            Event.objects.create(
                tenant=tenant,
                type="candidate_link_" + options["action"],
                payload={"application_id": str(link.application_id)},
            )
        self.stdout.write("Enlace actualizado. No se ha enviado ninguna comunicación.")
