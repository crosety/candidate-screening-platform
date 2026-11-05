from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from adapters.agent_langchain.copilot import BedrockCopilot
from adapters.fakes.chat_model import ScriptedChatModel
from adapters.fakes.tracing import InMemoryTraceSink
from screening.domain.errors import EvaluationFailed
from tests.contracts.test_langchain_runner import turn as turn

pytestmark = pytest.mark.asyncio


async def test_structured_suggestions_and_trace(turn) -> None:
    sink = InMemoryTraceSink()
    copilot = BedrockCopilot(
        ScriptedChatModel(responses=[]),
        sink,
        "gpt-6-luna",
        "medium",
        RunnableLambda(
            lambda _: {
                "raw": AIMessage(content="{}"),
                "parsed": {
                    "suggestions": [
                        {"field": "fit", "why": "Caso observado", "append": "Pregunta el turno."}
                    ]
                },
            }
        ),
    )
    ctx = replace(turn.run_context, agent="copiloto", session_id=None, granted=frozenset())
    assert (await copilot.suggest(ctx, "brief", "{}"))[0].field == "fit"
    assert sink.llm_calls[0].agent == "copiloto" and sink.llm_calls[0].session_id is None


async def test_unparsed_suggestions_fail(turn) -> None:
    copilot = BedrockCopilot(
        ScriptedChatModel(responses=[]),
        InMemoryTraceSink(),
        "gpt-6-luna",
        "medium",
        RunnableLambda(lambda _: {"raw": AIMessage(content="no"), "parsed": None}),
    )
    with pytest.raises(EvaluationFailed):
        await copilot.suggest(turn.run_context, "brief", "{}")


async def test_unknown_field_is_rejected_not_silently_filtered(turn, caplog) -> None:
    copilot = BedrockCopilot(
        ScriptedChatModel(responses=[]),
        InMemoryTraceSink(),
        "gpt-6-luna",
        "medium",
        RunnableLambda(
            lambda _: {
                "raw": AIMessage(content="{}"),
                "parsed": {
                    "suggestions": [
                        {"field": "grants", "why": "private evidence", "append": "unsafe"}
                    ]
                },
            }
        ),
    )
    with pytest.raises(EvaluationFailed, match="campos no válidos"):
        await copilot.suggest(turn.run_context, "brief", "{}")
    assert "reason=invalid_schema" in caplog.text
    assert "private evidence" not in caplog.text
