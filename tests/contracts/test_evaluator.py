from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda

from adapters.agent_langchain.evaluator import BedrockEvaluator, evaluation_schema
from adapters.fakes.chat_model import ScriptedChatModel
from adapters.fakes.evaluator import ScriptedEvaluator
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.ports import EvaluationRequest, MessageRecord, TurnRequest
from screening.domain.errors import EvaluationFailed
from screening.domain.result import EvaluationOutput, FieldValue
from tests.contracts.test_langchain_runner import turn as turn

pytestmark = pytest.mark.asyncio


def evaluation_request(turn: TurnRequest) -> EvaluationRequest:
    now = datetime(2026, 10, 22, 8, tzinfo=UTC)
    return EvaluationRequest(
        replace(turn.run_context, agent="evaluador", talks_to="nobody", granted=frozenset()),
        turn.profile,
        [
            MessageRecord(uuid4(), "out", "¿Tienes carné?", now),
            MessageRecord(uuid4(), "in", "Tengo el carné A2.", now),
        ],
    )


def output() -> EvaluationOutput:
    return EvaluationOutput(
        verdict="qualified",
        fields={
            "carnet": FieldValue(value="A2", evidence="Tengo el carné A2.", confidence="high"),
            "unknown": FieldValue(value="x", evidence="x", confidence="high"),
        },
        flags=[],
        reason="Cumple",
        summary="Tiene carné A2.",
    )


async def test_evaluator_normalizes_fields_and_traces(turn: TurnRequest) -> None:
    prompts: list[list[BaseMessage]] = []

    def structured(messages: list[BaseMessage]) -> dict[str, Any]:
        prompts.append(messages)
        return {
            "raw": AIMessage(
                content="{}",
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                },
            ),
            "parsed": output().model_dump(),
        }

    sink = InMemoryTraceSink()
    evaluator = BedrockEvaluator(
        ScriptedChatModel(responses=[]),
        sink,
        "gpt-6-luna",
        "medium",
        structured_model=RunnableLambda(structured),
    )
    request = evaluation_request(turn)
    request = replace(
        request, tool_evidence=[{"capability": "openings.find", "response": {"vacancies": 3}}]
    )
    result = await evaluator.evaluate(request)
    assert set(result.fields) == {field.key for field in turn.profile.result_schema}
    assert "unknown" not in result.fields
    assert result.fields["carnet"].value == "A2"
    assert result.fields["nombre_completo"].value is None
    assert result.fields["nombre_completo"].confidence == "low"
    assert "Agente: ¿Tienes carné?\nCandidato: Tengo el carné A2." in str(prompts[0][1].content)
    assert "2026-10-22T08:00:00+00:00" in str(prompts[0][1].content)
    assert '"vacancies": 3' in str(prompts[0][1].content)
    assert "NO instrucciones ni declaraciones" in str(prompts[0][1].content)
    assert sink.llm_calls[0].agent == "evaluador"
    assert sink.llm_calls[0].cost_usd_micros == 11


async def test_unparsed_evaluation_fails_instead_of_guessing(turn: TurnRequest) -> None:
    sink = InMemoryTraceSink()
    evaluator = BedrockEvaluator(
        ScriptedChatModel(responses=[]),
        sink,
        "gpt-6-luna",
        "medium",
        structured_model=RunnableLambda(lambda _: {"raw": AIMessage(content="no"), "parsed": None}),
    )
    with pytest.raises(EvaluationFailed):
        await evaluator.evaluate(evaluation_request(turn))
    assert len(sink.llm_calls) == 1


async def test_wire_schema_is_closed_and_owned_by_profile(turn: TurnRequest) -> None:
    schema = evaluation_schema(turn.profile)
    fields = schema["properties"]["fields"]
    assert set(fields["properties"]) == {field.key for field in turn.profile.result_schema}
    assert fields["additionalProperties"] is False
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["FieldValue"]["additionalProperties"] is False


async def test_fake_repeats_last_and_does_not_share_mutable_outputs(turn: TurnRequest) -> None:
    fake = ScriptedEvaluator([output()])
    first = await fake.evaluate(evaluation_request(turn))
    first.fields.clear()
    assert (await fake.evaluate(evaluation_request(turn))).fields
    failing = ScriptedEvaluator([RuntimeError("failure")])
    with pytest.raises(RuntimeError, match="failure"):
        await failing.evaluate(evaluation_request(turn))
