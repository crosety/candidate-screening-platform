import asyncio
from argparse import ArgumentParser
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.http import StreamingHttpResponse

from web.conversations.models import Session
from web.conversations.voice_api import complete
from web.tenancy.context import tenant_context
from web.tracing.models import LlmCall


class Command(BaseCommand):
    help = "One explicit real-model voice-text acceptance turn; never runs in tests or CI."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("tenant", type=UUID)
        parser.add_argument("session", type=UUID)

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.LLM_MODE != "openai":
            raise CommandError("Esta aceptación exige OpenAI real.")
        tenant, session = options["tenant"], options["session"]
        with tenant_context(tenant):
            row = Session.objects.select_related("application").get(pk=session)
            if not row.application.external_id.startswith("SIM-"):
                raise CommandError("Usa exclusivamente una candidatura sintética SIM-.")
            before = LlmCall.objects.aggregate(n=Sum("cost_usd_micros"))["n"] or 0

        async def run() -> str:
            response = await complete(
                tenant,
                session,
                [
                    {
                        "role": "user",
                        "content": "Prueba de voz: prefiero continuar por escrito. "
                        "¿Puedes confirmarlo brevemente?",
                    }
                ],
            )
            assert isinstance(response, StreamingHttpResponse) and response.is_async
            content = cast(AsyncIterator[bytes], response.streaming_content)
            return "".join([part.decode() async for part in content])

        stream = asyncio.run(run())
        with tenant_context(tenant):
            after = LlmCall.objects.aggregate(n=Sum("cost_usd_micros"))["n"] or 0
        self.stdout.write(f"SSE terminado: {stream.endswith('data: [DONE]' + chr(10) * 2)}")
        self.stdout.write(f"Coste adicional estimado: {after - before} microUSD")
