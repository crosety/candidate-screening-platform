from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from adapters.agent_langchain.judge import BedrockJudge
from adapters.agent_langchain.simulator import BedrockCandidateSimulator
from adapters.fakes.chat_model import ScriptedChatModel
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.prompts import build_judge_input
from screening.domain.evals import SILENCE, JudgeOutput, Persona
from tests.contracts.test_langchain_runner import turn as turn

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("name,full_name", [("Test", "Test Prueba"), ("Test Demo", "Test Demo")])
async def test_simulator_fin_and_trace(turn, name: str, full_name: str) -> None:
    sink = InMemoryTraceSink()
    prompts: list[str] = []

    def answer(messages: list[HumanMessage]) -> dict[str, object]:
        prompts.append(str(messages[0].content))
        return {"raw": AIMessage(content="{}"), "parsed": {"action": "end", "message": ""}}

    simulator = BedrockCandidateSimulator(
        ScriptedChatModel(responses=[]), sink, "gpt-6-luna", "medium", RunnableLambda(answer)
    )
    persona = Persona(
        key="test", name=name, facts="Hechos", style="Corto", expected_verdict="review"
    )
    assert (
        await simulator.next_message(
            replace(turn.run_context, session_id=None, agent="simulador"), persona, []
        )
        is None
    )
    assert sink.llm_calls[0].agent == "simulador"
    assert f"si te piden nombre y apellidos, usa {full_name}" in prompts[0]
    assert persona.name == name
    prompt = prompts[0]
    assert "Solo formulas preguntas concretas" not in prompt
    assert "No respondas [FIN] mientras" not in prompt
    assert "Das un dato cuando te lo preguntan" in prompt
    assert persona.facts == "Hechos"
    assert persona.expected_verdict == "review"


@pytest.mark.parametrize(
    ("parsed", "expected"),
    [
        ({"action": "reply", "message": " Sí, me viene bien. "}, "Sí, me viene bien."),
        ({"action": "silence", "message": "..."}, SILENCE),
        ({"action": "end", "message": "Gracias"}, None),
        ({"action": "reply", "message": ""}, None),
    ],
)
async def test_simulator_sends_only_the_structured_message(
    turn, parsed: dict[str, str], expected: str | None
) -> None:
    simulator = BedrockCandidateSimulator(
        ScriptedChatModel(responses=[]),
        InMemoryTraceSink(),
        "gpt-6-luna",
        "medium",
        RunnableLambda(lambda _: {"raw": AIMessage(content="{}"), "parsed": parsed}),
    )
    persona = Persona(key="t", name="Ana Gil", facts="f", style="s", expected_verdict="pool")
    ctx = replace(turn.run_context, session_id=None, agent="simulador")
    assert await simulator.next_message(ctx, persona, []) == expected


async def test_judge_prompt_and_normalization(turn) -> None:
    persona = Persona(
        key="test",
        name="Test",
        facts="Hechos",
        style="Corto",
        expected_verdict="review",
        must=["Saluda"],
    )
    prompt = build_judge_input(turn.profile, persona, [], None)
    assert "Debe: Saluda" in prompt and turn.profile.rubric[0].key in prompt
    sink = InMemoryTraceSink()
    judge = BedrockJudge(
        ScriptedChatModel(responses=[]),
        sink,
        "gpt-6-luna",
        "medium",
        RunnableLambda(
            lambda _: {
                "raw": AIMessage(content="raw"),
                "parsed": JudgeOutput(items=[], checks=[], comment=""),
            }
        ),
    )
    output = await judge.judge(turn.run_context, turn.profile, persona, [], None)
    assert not output.checks[0].passed and output.items[0].score == 0
