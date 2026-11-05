from pathlib import Path

import pytest

from adapters.fakes.evals import ScriptedSimulator
from screening.application.prompts import build_judge_input
from screening.domain.evals import SILENCE, Persona
from screening.domain.profile import ProfileContent, ReengagementPolicy
from tests.application.test_conversation_service import ScriptedAgentRunner, response
from tests.application.test_conversation_service import bundle as bundle
from tests.application.test_eval_service import EvalBundle
from tests.application.test_eval_service import eval_bundle as eval_bundle

pytestmark = pytest.mark.asyncio


@pytest.fixture
def content() -> ProfileContent:
    profile = ProfileContent.model_validate_json(
        (Path(__file__).resolve().parents[1] / "fixtures/grupo_sazon_legacy_v1.json").read_text()
    )
    profile.reengagement = ReengagementPolicy(enabled=True, max_attempts=2)
    return profile


async def test_silent_persona_receives_the_platform_reminder_turn(eval_bundle: EvalBundle) -> None:
    b = eval_bundle
    runner = ScriptedAgentRunner([response(f"Turno {i}") for i in range(20)])
    b.service.runner_factory = lambda gateway: runner
    b.service.simulator = ScriptedSimulator([SILENCE, "Perdona, sigo aquí", SILENCE, SILENCE])
    run = await b.service.start(b.bundle.tenant, ["one"], False, 1, b.bundle.user, enqueue=False)
    await b.service.execute(b.bundle.tenant, run.id)
    case = (await b.repo.list_cases(b.bundle.tenant, run.id))[0]
    texts = [item["text"] for item in case.transcript]
    assert [item.get("channel") for item in case.transcript].count("reminder") == 2
    # greeting, reminder 1, reply + answer, reminder 2; the third silence exceeds the policy.
    assert texts == ["Turno 0", "Turno 1", "Perdona, sigo aquí", "Turno 2", "Turno 3"]
    reminders = [r for r in runner.requests if r.text and r.text.startswith("[plataforma]")]
    assert len(reminders) == 2
    assert all(not r.run_context.granted for r in reminders)


async def test_judge_sees_reminders_but_not_the_expected_verdict(
    eval_bundle: EvalBundle, content: ProfileContent
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from screening.application.ports import MessageRecord

    now = datetime.now(UTC)
    persona = Persona(key="p", name="P", facts="f", style="s", expected_verdict="qualified")
    text = build_judge_input(
        content,
        persona,
        [
            MessageRecord(uuid4(), "out", "¿Tienes carnet?", now),
            MessageRecord(uuid4(), "out", "¿Sigues ahí?", now, channel="reminder"),
        ],
        None,
    )
    assert "Veredicto esperado" not in text and "qualified" not in text
    assert "Agente (recordatorio programado tras un silencio del candidato): ¿Sigues ahí?" in text


async def test_eval_tool_evidence_reaches_evaluator_and_judge_like_production(
    content: ProfileContent,
) -> None:
    from uuid import uuid4

    from adapters.fakes.tracing import InMemoryTraceSink
    from screening.application.evals import _EvidenceSink
    from screening.application.memory import InMemoryConversationRepository
    from screening.application.ports import CapabilityCallRecord

    inner, repo = InMemoryTraceSink(), InMemoryConversationRepository()
    sink = _EvidenceSink(inner, repo)
    tenant, session = uuid4(), uuid4()
    for capability, status in (
        ("openings.find", "ok"),
        ("openings.find", "error"),
        ("interview.book", "ok"),
    ):
        await sink.record_capability_call(
            CapabilityCallRecord(
                tenant, session, capability, "locales", status, 3, {"city": "Torrent"}, {"n": 1}
            )
        )
    assert len(inner.capability_calls) == 3  # every call stays traced
    # The judge sees every successful tool result, bookings included; the evaluator only reads.
    assert [e["capability"] for e in sink.judge_evidence] == ["openings.find", "interview.book"]
    assert repo.evidence[(tenant, session)] == [
        {"capability": "openings.find", "request": {"city": "Torrent"}, "response": {"n": 1}}
    ]
    persona = Persona(key="p", name="P", facts="f", style="s", expected_verdict="pool")
    text = build_judge_input(content, persona, [], None, repo.evidence[(tenant, session)])
    assert "Resultados de herramientas autorizadas" in text and "Torrent" in text
