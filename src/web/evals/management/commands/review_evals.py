"""Explicit, bounded real acceptance run; never publishes or rewrites a profile."""

import asyncio
import json
import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from adapters.persistence.evals_gate import django_evals_gate
from screening.platform.container import get_container
from web.accounts.models import User
from web.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Repite las evals reales de cada cliente, sin publicar y con presupuesto explícito."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--allow-live", action="store_true")
        parser.add_argument("--max-cost-usd", type=float, default=0.6)
        parser.add_argument("--tenants", nargs="+", required=True)
        parser.add_argument("--actor", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["allow_live"] or settings.LLM_MODE != "openai":
            raise CommandError("Necesita --allow-live y LLM_MODE=openai.")
        budget = options["max_cost_usd"]
        if not 0.2 <= budget <= 0.6:
            raise CommandError("El presupuesto total debe estar entre 0,20 y 0,60 USD.")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        slugs = set(options["tenants"])
        tenants = list(Tenant.objects.filter(slug__in=slugs))
        if len(tenants) != len(slugs) or not tenants or budget / len(tenants) < 0.1:
            raise CommandError("Cliente no encontrado o presupuesto insuficiente para las suites.")
        user = User.objects.get(email=options["actor"])
        container = get_container()

        async def run() -> None:
            total = 0
            for tenant in tenants:
                evaluation = await container.evals.start(
                    tenant.id, None, False, budget / len(tenants), user.id, enqueue=False
                )
                self.stdout.write(
                    json.dumps({"started": str(evaluation.id), "tenant": tenant.slug})
                )
                await container.evals.execute(tenant.id, evaluation.id)
                result = await container.evals.evals.get_run(tenant.id, evaluation.id)
                cases = await container.evals.evals.list_cases(tenant.id, evaluation.id)
                cost = sum(c.cost_usd_micros for c in cases)
                total += cost
                self.stdout.write(
                    json.dumps(
                        {
                            "tenant": tenant.slug,
                            "run_id": str(result.id),
                            "status": result.status,
                            "score": result.score_draft,
                            "cost_usd_micros": cost,
                            "gate": await sync_to_async(django_evals_gate)(
                                tenant.id, result.draft_rev
                            ),
                            "cases": [
                                {
                                    "key": c.persona_key,
                                    "status": c.status,
                                    "score": c.score,
                                    "verdict": c.verdict,
                                }
                                for c in cases
                            ],
                        },
                        ensure_ascii=False,
                    )
                )
                if total >= budget * 1_000_000:
                    raise CommandError("Presupuesto agotado; no se inicia otra suite.")
            self.stdout.write(json.dumps({"total_cost_usd_micros": total}))

        asyncio.run(run())
