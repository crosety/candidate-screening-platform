from typing import Any

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError, CommandParser

from screening.platform.container import get_container
from web.tenancy.models import Membership, Tenant


class Command(BaseCommand):
    help = "Ejecuta las evals de un cliente sin encolarlas."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--offer", default="")
        parser.add_argument("--personas")
        parser.add_argument("--max-cost", type=float, default=1.0)
        parser.add_argument("--no-compare", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            tenant = Tenant.objects.get(slug=options["tenant"])
        except Tenant.DoesNotExist as exc:
            raise CommandError("Cliente no encontrado") from exc
        member = Membership.objects.filter(tenant=tenant, role="fde").order_by("created_at").first()
        if member is None:
            raise CommandError("No hay un FDE para este cliente")
        service = get_container().evals
        run = async_to_sync(service.start)(
            tenant.id,
            options["personas"].split(",") if options["personas"] else None,
            not options["no_compare"],
            options["max_cost"],
            member.user_id,
            enqueue=False,
            offer_key=options["offer"],
        )
        async_to_sync(service.execute)(tenant.id, run.id)
        for case in async_to_sync(service.evals.list_cases)(tenant.id, run.id):
            self.stdout.write(
                f"{case.persona_key}\t{case.target}\t{case.verdict}\t{case.score}\t"
                f"{case.cost_usd_micros} microUSD\t{case.status}"
            )
        result = async_to_sync(service.evals.get_run)(tenant.id, run.id)
        self.stdout.write(
            f"RUN {result.id} {result.status}: borrador={result.score_draft}, "
            f"publicada={result.score_published}, "
            f"coste={result.summary.get('cost_usd_micros')} microUSD"
        )
