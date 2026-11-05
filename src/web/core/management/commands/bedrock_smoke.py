import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from langchain_core.messages import HumanMessage
from langchain_core.messages.ai import UsageMetadata

from adapters.llm.bedrock import build_chat_model
from adapters.llm.pricing import cost_usd_micros


class Command(BaseCommand):
    help = "Comprueba Bedrock con una única petición breve al guardián."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            model = build_chat_model("guardian")
            result = model.invoke([HumanMessage(content="Responde solo con la palabra: listo")])
            usage: UsageMetadata = result.usage_metadata or {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
            details = usage.get("input_token_details", {})
            cache_read = details.get("cache_read", 0)
            cache_write = details.get("cache_creation", 0)
            cost = cost_usd_micros(
                model.model,
                usage.get("input_tokens", 0) - cache_read - cache_write,
                usage.get("output_tokens", 0),
                cache_read,
                cache_write,
            )
            self.stdout.write(
                json.dumps(
                    {
                        "model": model.model,
                        "text": result.text,
                        "usage_metadata": usage,
                        "stop_reason": result.response_metadata.get("stop_reason"),
                        "cost_usd_micros": cost,
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:
            raise CommandError(f"{type(exc).__name__}: {exc}") from None
