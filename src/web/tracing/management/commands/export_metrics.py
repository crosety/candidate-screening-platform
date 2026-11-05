from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from adapters.persistence.tracing import enqueue_llm_export
from screening.platform.container import get_container
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant
from web.tracing.models import LlmCall


class Command(BaseCommand):
    help = "Reconciliar métricas locales con Langfuse; vista previa salvo --apply."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--since", required=True, help="ISO 8601 con zona horaria")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--retry", action="store_true", help="Reactivar trabajos agotados")

    def handle(self, *args: Any, **options: Any) -> None:
        since = parse_datetime(options["since"])
        if since is None or timezone.is_naive(since) or not 1 <= options["limit"] <= 1000:
            raise CommandError("Fecha o límite no válido (1–1000).")
        if not settings.LANGFUSE_ENABLED or not settings.LANGFUSE_PROJECT_ID:
            raise CommandError("Configura primero el proyecto de métricas.")
        tenant = Tenant.objects.get(slug=options["tenant"])
        count = 0
        with tenant_context(tenant.id):
            for row in LlmCall.objects.filter(created_at__gte=since).order_by("created_at")[
                : options["limit"]
            ]:
                count += 1
                if options["apply"]:
                    enqueue_llm_export(row)
                    if options["retry"]:
                        Job.objects.filter(
                            tenant=tenant,
                            kind="langfuse_export",
                            status="dead",
                            idempotency_key=f"langfuse:{settings.LANGFUSE_PROJECT_ID}:{row.id}",
                        ).update(
                            status="queued",
                            attempts=0,
                            run_at=get_container().clock.now(),
                            last_error="",
                        )
        self.stdout.write(f"{count} métricas seleccionadas; aplicar={options['apply']}.")
