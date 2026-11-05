import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from langchain_core.messages import HumanMessage

from adapters.llm.openai import MODEL_ID, ROLE_EFFORT, build_openai_model
from adapters.llm.pricing import cost_usd_micros


class Command(BaseCommand):
    help = "Comprueba OpenAI Terra medium (guardián) con una única petición breve."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = build_openai_model("guardian").invoke(
                [HumanMessage(content="Responde solo con la palabra: listo")]
            )
        except Exception as exc:
            # Provider exceptions must never echo request headers or configuration.
            raise CommandError(f"OpenAI smoke falló: {type(exc).__name__}") from None
        usage = result.usage_metadata
        if usage is None or not result.text:
            raise CommandError("OpenAI no devolvió texto y uso verificables.")
        details = usage.get("input_token_details", {})
        read, write = details.get("cache_read", 0), details.get("cache_creation", 0)
        cost = cost_usd_micros(
            MODEL_ID, usage["input_tokens"] - read - write, usage["output_tokens"], read, write
        )
        self.stdout.write(
            json.dumps(
                {
                    "model": MODEL_ID,
                    "effort": ROLE_EFFORT["guardian"],
                    "text": result.text,
                    "usage": usage,
                    "cost_usd_micros": cost,
                },
                ensure_ascii=False,
            )
        )
