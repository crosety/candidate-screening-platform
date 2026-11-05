"""Explicit model configuration for the historical Luna recordings, not runtime acceptance."""

from langchain_openai import ChatOpenAI

from adapters.llm.openai import build_openai_model

RECORDED_MODEL_ID = "gpt-6-luna"
RECORDED_EFFORT = {"conversador": "low", "guardian": "low", "evaluador": "medium"}


def recorded_luna_model(role: str) -> ChatOpenAI:
    return build_openai_model(role).model_copy(
        update={
            "model_name": RECORDED_MODEL_ID,
            "reasoning": {"effort": RECORDED_EFFORT[role]},
        }
    )
