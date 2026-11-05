from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from adapters.agent_langchain.runner import LangChainAgentRunner
from adapters.fakes.chat_model import ScriptedChatModel
from adapters.fakes.connectors import StaticConnectorRegistry
from adapters.fakes.evals import (
    InMemoryEvalRepository,
    PerfectJudge,
    ScriptedSimulator,
    StaticPersonaSource,
)
from adapters.fakes.evaluator import ScriptedEvaluator
from adapters.fakes.guard import ScriptedReplyGuard
from adapters.fakes.jobs import InMemoryJobQueue
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.capability_gateway import CapabilityGateway
from screening.application.evals import EvalConnectorRegistry, EvalService
from screening.application.ports import MessageRecord, ResultRecord, TurnRequest, TurnResult
from screening.domain.errors import NotAllowed
from screening.domain.evals import JudgeOutput, Persona
from screening.domain.profile import ProfileContent
from screening.domain.result import EvaluationOutput, FieldValue
from screening.domain.run_context import RunContext
from tests.application.test_conversation_service import Bundle, response
from tests.application.test_conversation_service import bundle as bundle
from tests.application.test_conversation_service import content as content

pytestmark = pytest.mark.asyncio


@dataclass
class EvalBundle:
    service: EvalService
    repo: InMemoryEvalRepository
    bundle: Bundle
    registry: StaticConnectorRegistry
    queue: InMemoryJobQueue


@pytest.fixture
def eval_bundle(bundle: Bundle) -> EvalBundle:
    repo, sink, queue = InMemoryEvalRepository(), InMemoryTraceSink(), InMemoryJobQueue()
    registry = StaticConnectorRegistry({})
    personas = StaticPersonaSource(
        [
            Persona(
                key=key,
                name=key,
                facts="No tiene carné",
                style="Breve",
                expected_verdict="not_qualified",
            )
            for key in ("one", "two")
        ]
    )

    def factory(gateway):
        model = ScriptedChatModel(
            responses=[
                AIMessage(content="Hola, soy asistente virtual."),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "screening_complete",
                            "args": {},
                            "id": "complete",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Gracias, hasta pronto."),
            ]
        )
        return LangChainAgentRunner(
            model,
            ScriptedReplyGuard(),
            sink,
            gateway,
            "gpt-6-luna",
            "medium",
            anthropic_caching=False,
        )

    service = EvalService(
        repo,
        bundle.service.versions,
        personas,
        bundle.repo,
        registry,
        factory,
        ScriptedEvaluator(
            [
                EvaluationOutput(
                    verdict="not_qualified",
                    fields={
                        "carnet": FieldValue(
                            value=False, evidence="No tengo carné", confidence="high"
                        )
                    },
                    flags=[],
                    reason="Sin carné",
                    summary="Sin carné",
                )
            ]
        ),
        ScriptedSimulator(["No tengo carné"] * 30),
        PerfectJudge(),
        sink,
        bundle.profiles.clock,
        queue,
        "gpt-6-luna",
    )
    return EvalBundle(service, repo, bundle, registry, queue)


async def test_start_creates_explicit_personas_and_comparisons(eval_bundle: EvalBundle) -> None:
    b = eval_bundle
    run = await b.service.start(b.bundle.tenant, None, True, 1, b.bundle.user)
    assert run.persona_keys == ["one", "two"]
    assert len(await b.repo.list_cases(b.bundle.tenant, run.id)) == 4
    assert b.queue.jobs[0]["max_attempts"] == 1


async def test_start_rejects_unknown_persona_and_invalid_cost(eval_bundle: EvalBundle) -> None:
    b = eval_bundle
    with pytest.raises(NotAllowed, match="Persona desconocida"):
        await b.service.start(b.bundle.tenant, ["unknown"], False, 1, b.bundle.user)
    with pytest.raises(NotAllowed, match="coste"):
        await b.service.start(b.bundle.tenant, None, False, 30, b.bundle.user)


async def test_execute_runs_the_real_agent_and_assessment_once(eval_bundle: EvalBundle) -> None:
    b = eval_bundle
    run = await b.service.start(b.bundle.tenant, None, False, 1, b.bundle.user, enqueue=False)
    await b.service.execute(b.bundle.tenant, run.id)
    await b.service.execute(b.bundle.tenant, run.id)
    result = await b.repo.get_run(b.bundle.tenant, run.id)
    assert result.status == "done" and result.score_draft == 100
    cases = await b.repo.list_cases(b.bundle.tenant, run.id)
    assert all(case.verdict == "not_qualified" and len(case.transcript) == 3 for case in cases)
    assert len(b.bundle.repo.sessions) == 1
    assert not b.queue.jobs and not b.registry.calls


async def test_budget_exhaustion_marks_remaining_cases(eval_bundle: EvalBundle) -> None:
    b = eval_bundle
    run = await b.service.start(b.bundle.tenant, None, False, 0.1, b.bundle.user)
    cases = await b.repo.list_cases(b.bundle.tenant, run.id)
    b.repo.costs[cases[0].id] = run.max_cost_usd_micros
    await b.service.execute(b.bundle.tenant, run.id)
    assert (await b.repo.get_run(b.bundle.tenant, run.id)).status == "failed"
    assert all(
        c.judge["error"] == "Presupuesto agotado"
        for c in await b.repo.list_cases(b.bundle.tenant, run.id)
    )


async def test_eval_connector_never_writes(eval_bundle: EvalBundle) -> None:
    b = eval_bundle
    profile = b.bundle.profiles.get_draft(b.bundle.tenant).content
    persona = b.service.personas.list_personas("test")[0]
    registry = EvalConnectorRegistry(b.registry, persona, b.bundle.profiles.clock)
    connector = registry.get("test", profile.connections[0])
    assert (await connector.call("write_result", {}))["status"] == "ok"
    assert (await connector.call("set_stage", {}))["status"] == "ok"
    assert (await connector.call("get_application", {}))["candidate_name"] == "one"
    with pytest.raises(NotAllowed):
        await connector.call("send_message", {})
    assert not b.registry.calls


async def test_one_failed_case_does_not_stop_the_others(eval_bundle: EvalBundle) -> None:
    b = eval_bundle
    original = b.service.runner_factory
    calls = 0

    def factory(gateway):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Error de un caso")
        return original(gateway)

    b.service.runner_factory = factory
    run = await b.service.start(b.bundle.tenant, None, False, 1, b.bundle.user)
    await b.service.execute(b.bundle.tenant, run.id)
    result = await b.repo.get_run(b.bundle.tenant, run.id)
    assert (
        result.status == "failed" and result.summary["done"] == 1 and result.summary["errors"] == 1
    )


async def test_eval_uses_runtime_interview_lifecycle_and_records_the_candidate_evidence(
    eval_bundle: EvalBundle,
) -> None:
    b = eval_bundle
    profile = b.bundle.profiles.get_draft(b.bundle.tenant).content.model_copy(deep=True)
    # This fixture has one requirement, supported by the synthetic candidate's literal answer.
    profile.result_schema = [field for field in profile.result_schema if field.key == "carnet"]
    b.service.personas = StaticPersonaSource(
        [
            Persona(
                key="agenda",
                name="Agenda",
                facts="Tiene carné",
                style="Breve",
                expected_verdict="qualified",
            )
        ]
    )
    b.service.evaluator = ScriptedEvaluator(
        [
            EvaluationOutput(
                verdict="qualified",
                fields={
                    "carnet": FieldValue(value=True, evidence="Sí, tengo carné", confidence="high")
                },
                flags=[],
                reason="Cumple",
                summary="Tiene carné",
            )
        ]
    )
    b.service.simulator = ScriptedSimulator(["Sí, tengo carné"])
    b.registry.responses["list_slots"] = {
        "slots": [
            {
                "slot_id": "first",
                "site_id": "local",
                "site_name": "Local Centro",
                "site_address": "Calle Mayor 1",
                "site_timezone": "Europe/Madrid",
                "starts_at": "2026-10-30T09:00:00+00:00",
                "starts_at_local": "viernes 30/10/2026 10:00",
                "duration_minutes": 15,
            },
            {
                "slot_id": "second",
                "site_id": "local",
                "site_name": "Local Centro",
                "site_address": "Calle Mayor 1",
                "site_timezone": "Europe/Madrid",
                "starts_at": "2026-10-30T10:00:00+00:00",
                "starts_at_local": "viernes 30/10/2026 11:00",
                "duration_minutes": 15,
            },
        ]
    }
    bookings: list[dict[str, Any]] = []
    views: list[dict[str, Any] | None] = []
    judged: list[dict[str, Any]] = []
    sink = InMemoryTraceSink()
    b.service.sink = sink

    class EvidenceJudge(PerfectJudge):
        async def judge(
            self,
            ctx: RunContext,
            profile: ProfileContent,
            persona: Persona,
            transcript: list[MessageRecord],
            result: ResultRecord | None,
            evidence: list[dict[str, Any]] | None = None,
        ) -> JudgeOutput:
            judged.extend(evidence or [])
            return await super().judge(ctx, profile, persona, transcript, result, evidence)

    b.service.judge = EvidenceJudge()

    class BookingRunner:
        def __init__(self, gateway: CapabilityGateway) -> None:
            self.gateway = gateway

        async def run_turn(self, request: TurnRequest) -> TurnResult:
            if request.text is None:
                return response("¿Tienes carné?")
            ctx = request.run_context
            await self.gateway.call(ctx, request.profile, "screening_complete", {})
            await self.gateway.call(ctx, request.profile, "interview_slots", {"site_id": "local"})
            bookings.append(
                await self.gateway.call(
                    ctx, request.profile, "interview_book", {"slot_id": "first"}
                )
            )
            interviews = self.gateway.interviews
            if interviews is None or ctx.session_id is None or ctx.application_id is None:
                raise RuntimeError("La agenda no está disponible")
            views.append(await interviews.candidate_view(ctx.tenant_id, ctx.application_id))
            # These are the public candidate actions used when reopening a completed chat.
            await interviews.reopen(ctx.tenant_id, ctx.session_id)
            bookings.append(
                await self.gateway.call(
                    ctx, request.profile, "interview_reschedule", {"slot_id": "second"}
                )
            )
            views.append(await interviews.candidate_view(ctx.tenant_id, ctx.application_id))
            await interviews.reopen(ctx.tenant_id, ctx.session_id)
            bookings.append(await self.gateway.call(ctx, request.profile, "interview_cancel", {}))
            views.append(await interviews.candidate_view(ctx.tenant_id, ctx.application_id))
            return response("Tu entrevista queda cancelada.")

    b.service.runner_factory = BookingRunner
    run = await b.service.start(
        b.bundle.tenant, None, False, 1, b.bundle.user, enqueue=False, alternative=profile
    )
    await b.service.execute(b.bundle.tenant, run.id)
    cases = await b.repo.list_cases(b.bundle.tenant, run.id)
    assert [case.status for case in cases] == ["done"]
    assert bookings[0]["status"] == "booked"
    assert bookings[0]["confirmation"]["when"] == "viernes 30/10/2026 a las 10:00"
    assert bookings[0]["confirmation"]["address"] == "Calle Mayor 1"
    assert bookings[0]["reminder"] == {"scheduled": True, "at": "jueves 29/10/2026 a las 10:00"}
    assert bookings[0]["booking_id"]
    assert [booking["status"] for booking in bookings] == ["booked", "rescheduled", "cancelled"]
    assert len({booking["booking_id"] for booking in bookings}) == 1
    assert [view["status"] for view in views if view is not None] == [
        "booked",
        "rescheduled",
        "cancelled",
    ]
    assert views[-1] is not None and views[-1]["can_change"] is False
    assert views[-1]["when"] == "viernes 30/10/2026 a las 11:00"
    tool_evidence = [
        call.response
        for call in sink.capability_calls
        if call.request["tool"] in {"interview_book", "interview_reschedule", "interview_cancel"}
    ]
    assert tool_evidence == bookings
    assert [
        item["response"]
        for item in judged
        if item["request"]["tool"] in {"interview_book", "interview_reschedule", "interview_cancel"}
    ] == bookings
    assert [operation for operation, _ in b.registry.calls] == ["list_slots"]
    assert not b.queue.jobs


async def test_eval_calendar_reschedules_cancels_and_replays_without_external_writes(
    eval_bundle: EvalBundle,
) -> None:
    b = eval_bundle
    profile = b.bundle.profiles.get_draft(b.bundle.tenant).content
    calendar = next(
        connection for connection in profile.connections if connection.kind == "calendar"
    )
    b.registry.responses["list_slots"] = {
        "slots": [
            {
                "slot_id": "first",
                "site_id": "local",
                "site_name": "Centro",
                "starts_at": "2026-10-30T09:00:00+00:00",
            },
            {
                "slot_id": "second",
                "site_id": "local",
                "site_name": "Centro",
                "starts_at": "2026-10-30T10:00:00+00:00",
            },
        ]
    }
    persona = b.service.personas.list_personas("test")[0]
    registry = EvalConnectorRegistry(b.registry, persona, b.bundle.profiles.clock)
    connector = registry.get("test", calendar)
    await connector.call("list_slots", {"site_id": "local"})
    book = {"slot_id": "first", "application_external_id": "candidate", "idempotency_key": "book"}
    first = await connector.call("book_slot", book)
    assert await registry.get("test", calendar).call("book_slot", book) == {
        **first,
        "replayed": True,
    }
    assert [
        slot["slot_id"]
        for slot in (await connector.call("list_slots", {"site_id": "local"}))["slots"]
    ] == ["second"]
    move = {
        "slot_id": "first",
        "new_slot_id": "second",
        "application_external_id": "candidate",
        "idempotency_key": "move",
    }
    moved = await connector.call("reschedule_booking", move)
    assert moved["status"] == "rescheduled" and moved["slot_id"] == "second"
    assert await connector.call("reschedule_booking", move) == {**moved, "replayed": True}
    cancel = {
        "slot_id": "second",
        "application_external_id": "candidate",
        "idempotency_key": "cancel",
    }
    cancelled = await connector.call("cancel_booking", cancel)
    assert cancelled == {"status": "cancelled", "slot_id": "second"}
    assert await connector.call("cancel_booking", cancel) == {**cancelled, "replayed": True}
    assert [
        slot["slot_id"]
        for slot in (await connector.call("list_slots", {"site_id": "local"}))["slots"]
    ] == ["first", "second"]
    assert {operation for operation, _ in b.registry.calls} == {"list_slots"}


async def test_eval_calendar_isolates_cases_connections_and_booking_ownership(
    eval_bundle: EvalBundle,
) -> None:
    b = eval_bundle
    profile = b.bundle.profiles.get_draft(b.bundle.tenant).content
    calendar = next(
        connection for connection in profile.connections if connection.kind == "calendar"
    )
    b.registry.responses["list_slots"] = {
        "slots": [
            {
                "slot_id": "shared-id",
                "site_id": "local",
                "site_name": "Centro",
                "starts_at": "2026-10-30T09:00:00+00:00",
            }
        ]
    }
    persona = b.service.personas.list_personas("test")[0]
    first_case = EvalConnectorRegistry(b.registry, persona, b.bundle.profiles.clock)
    second_case = EvalConnectorRegistry(b.registry, persona, b.bundle.profiles.clock)
    first = first_case.get("test", calendar)
    await first.call("list_slots", {"site_id": "local"})
    owner = {"slot_id": "shared-id", "application_external_id": "owner", "idempotency_key": "book"}
    assert (await first.call("book_slot", owner))["status"] == "booked"
    assert await first.call(
        "cancel_booking",
        {**owner, "application_external_id": "other", "idempotency_key": "foreign"},
    ) == {"status": "not_owner"}
    with pytest.raises(NotAllowed, match="otra operación"):
        await first.call("cancel_booking", owner)
    assert (await first.call("list_slots", {"site_id": "local"}))["slots"] == []
    for other in (
        second_case.get("test", calendar),
        first_case.get("test", calendar.model_copy(update={"id": "another-calendar"})),
        first_case.get("another-tenant", calendar),
    ):
        await other.call("list_slots", {"site_id": "local"})
        assert (await other.call("book_slot", owner))["status"] == "booked"
    assert {operation for operation, _ in b.registry.calls} == {"list_slots"}
