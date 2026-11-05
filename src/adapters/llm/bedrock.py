from typing import Literal

from django.conf import settings
from langchain_aws import ChatAnthropicBedrock

MODEL_IDS = {
    "conversador": "eu.anthropic.claude-sonnet-5-5",
    "guardian": "eu.anthropic.claude-haiku-5-5",
    "evaluador": "eu.anthropic.claude-opus-5-5",
    "juez": "eu.anthropic.claude-opus-5-5",
    "simulador": "eu.anthropic.claude-haiku-5-5",
    "copiloto": "eu.anthropic.claude-opus-5-5",
    "senales": "eu.anthropic.claude-haiku-5-5",
}

ROLE_CONFIG: dict[str, tuple[Literal["low", "medium", "high"], int]] = {
    "conversador": ("low", 2048),
    "guardian": ("low", 512),
    "evaluador": ("high", 8192),
    "juez": ("high", 4096),
    "simulador": ("low", 512),
    "copiloto": ("medium", 4096),
    "senales": ("low", 512),
}


def build_chat_model(role: str) -> ChatAnthropicBedrock:
    effort, max_tokens = ROLE_CONFIG[role]
    return ChatAnthropicBedrock(
        model_name=MODEL_IDS[role],
        region_name=settings.AWS_REGION,
        effort=effort,
        max_tokens_to_sample=max_tokens,
        max_retries=2,
        timeout=120 if role in ("evaluador", "juez") else 60,
        stop=None,
    )
