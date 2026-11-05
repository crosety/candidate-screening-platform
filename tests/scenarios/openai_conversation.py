import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from adapters.agent_langchain.guard import BedrockReplyGuard
from adapters.agent_langchain.runner import LangChainAgentRunner
from adapters.fakes.clock import FixedClock
from adapters.fakes.connectors import StaticConnectorRegistry
from adapters.fakes.conversations import InMemoryConversationRepository
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.capability_gateway import (
    CapabilityGateway,
    DenyBooking,
    PendingAssessment,
)
from screening.application.ports import (
    AgentRunner,
    NewApplication,
    SerializedMessage,
    TenantRecord,
    TurnRequest,
    TurnResult,
)
from screening.application.prompts import build_system_prompt, build_turn_note
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext
from tests.scenarios.legacy_openai import (
    RECORDED_EFFORT,
    RECORDED_MODEL_ID,
    recorded_luna_model,
)


def load_profile() -> ProfileContent:
    return ProfileContent.model_validate_json(
        (Path(__file__).resolve().parents[1] / "fixtures/grupo_sazon_legacy_v1.json").read_text()
    )


async def scenario() -> tuple[LangChainAgentRunner, RunContext, ProfileContent, InMemoryTraceSink]:
    profile = load_profile()
    clock = FixedClock(datetime(2026, 10, 22, 8, tzinfo=UTC))
    repo, sink = InMemoryConversationRepository(), InMemoryTraceSink()
    tenant = UUID(int=1)
    repo.add_tenant(TenantRecord(tenant, "grupo_sazon", "Grupo Sazón"))
    app = await repo.upsert_application(
        tenant, NewApplication("lucia", "Lucía", "", "València", "repartidores")
    )
    ctx = RunContext(
        "conversador",
        tenant,
        "grupo_sazon",
        "tenant",
        "candidate",
        UUID(int=2),
        app.id,
        UUID(int=3),
        frozenset(profile.grants.conversador),
        profile.autonomy.model_dump(),
    )
    registry = StaticConnectorRegistry(
        {
            "find_openings": {
                "status": "ok",
                "openings": [
                    {
                        "site_id": "benimaclet",
                        "name": "Sazón Benimaclet",
                        "travel_minutes": 1,
                        "vacancies": {"afternoon": 2},
                    },
                    {
                        "site_id": "centro",
                        "name": "Sazón Centro",
                        "travel_minutes": 12,
                        "vacancies": {"afternoon": 1},
                    },
                ],
            },
            "get_application": {"name": "Lucía", "city": "València", "job_ref": "repartidores"},
            "list_slots": {"status": "ok", "slots": []},
        }
    )
    gateway = CapabilityGateway(repo, registry, sink, clock, DenyBooking(), PendingAssessment())
    runner = LangChainAgentRunner(
        # Preserve the recorded Luna baseline; it is not acceptance of the active Terra runtime.
        recorded_luna_model("conversador"),
        BedrockReplyGuard(
            recorded_luna_model("guardian"), sink, RECORDED_MODEL_ID, RECORDED_EFFORT["guardian"]
        ),
        sink,
        gateway,
        RECORDED_MODEL_ID,
        RECORDED_EFFORT["conversador"],
        anthropic_caching=False,
    )
    return runner, ctx, profile, sink


async def run_scenario(
    runner: AgentRunner,
    ctx: RunContext,
    profile: ProfileContent,
    system_prompt: str,
    clock: FixedClock,
) -> list[TurnResult]:
    history: list[SerializedMessage] = []
    results = []
    for text in (
        None,
        "Hola, soy Lucía. Vivo en Benimaclet y tengo el carné A2.",
        "Prefiero tardes. Podría empezar la semana que viene.",
    ):
        result = await runner.run_turn(
            TurnRequest(
                ctx,
                profile,
                system_prompt,
                history,
                text,
                build_turn_note(clock.now(), "Europe/Madrid"),
            )
        )
        results.append(result)
        history = [
            SerializedMessage(**item)
            for item in json.loads(
                json.dumps([asdict(m) for m in [*history, *result.new_messages]])
            )
        ]
    return results


async def exercise() -> tuple[list[TurnResult], InMemoryTraceSink]:
    runner, ctx, profile, sink = await scenario()
    results = await run_scenario(
        runner,
        ctx,
        profile,
        build_system_prompt(profile, "Grupo Sazón", "web"),
        FixedClock(datetime(2026, 10, 22, 8, tzinfo=UTC)),
    )
    assert all(result.reply and not result.flags for result in results)
    assert "asistente virtual" in (results[0].reply or "").lower()
    assert "persona" not in (results[0].reply or "").lower()
    assert "human" not in (results[0].reply or "").lower()
    assert "?" in (results[0].reply or "")
    assert {call.agent for call in sink.llm_calls} == {"conversador", "guardian"}
    return results, sink
