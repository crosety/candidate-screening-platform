import json
import os
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from screening.domain.offer_configuration import ConfigurationPlan, prepare_configuration
from screening.platform.container import get_container
from web.tenancy.models import Membership, Tenant


class Command(BaseCommand):
    help = "Prepara empresa/ofertas en un nuevo borrador; nunca publica ni reescribe sesiones."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("plan", type=Path)
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--expected-rev", type=int, required=True)
        parser.add_argument("--backup", type=Path)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        tenant = Tenant.objects.get(slug=options["tenant"])
        member = Membership.objects.filter(
            tenant=tenant, user__email=options["actor"], role__in=["fde", "admin"]
        ).first()
        if member is None:
            raise CommandError("El actor debe ser FDE o administrador del cliente.")
        service = get_container().profiles
        draft = service.get_draft(tenant.id)
        if draft.rev != options["expected_rev"]:
            raise CommandError("El borrador ha cambiado; revisa el plan antes de continuar.")
        plan = ConfigurationPlan.model_validate_json(options["plan"].read_text())
        content = prepare_configuration(draft.content, plan)
        if content == draft.content:
            self.stdout.write("El borrador ya utiliza la configuración explícita. Sin cambios.")
            return
        self.stdout.write(f"Preparadas {len(content.offers)} ofertas; solo cambia el borrador.")
        if not options["apply"]:
            self.stdout.write("Validación sin escritura. Usa --apply y --backup para guardar.")
            return
        backup = options["backup"]
        if backup is None:
            raise CommandError("Es obligatorio indicar una copia --backup nueva.")
        # Exclusive creation and private mode prevent overwrite or accidental exposure.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(
                {
                    "tenant_id": str(tenant.id),
                    "rev": draft.rev,
                    "content": draft.content.model_dump(mode="json"),
                },
                handle,
                ensure_ascii=False,
            )
        saved = service.save_draft(tenant.id, draft.rev, content, member.user_id)
        self.stdout.write(f"Guardado borrador revisión {saved.rev}. Publicación pendiente.")
