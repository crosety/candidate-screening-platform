from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from adapters.fakes.clock import FixedClock
from adapters.fakes.connectors import (
    FailingConnectorRegistry,
    SlowConnectorRegistry,
    StaticConnectorRegistry,
)
from adapters.fakes.conversations import InMemoryConversationRepository
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.capability_gateway import (
    CapabilityGateway,
    DenyBooking,
    PendingAssessment,
)
from screening.application.ports import ConnectorRegistry, NewApplication, TenantRecord
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext

pytestmark = pytest.mark.asyncio


@dataclass
class Bundle:
    repo: InMemoryConversationRepository
    ctx: RunContext
    profile: ProfileContent
    clock: FixedClock
    sink: InMemoryTraceSink


@pytest.fixture
def content() -> ProfileContent:
    return ProfileContent.model_validate_json(
        (Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon/profile.json").read_text()
    )


@pytest.fixture
async def bundle(content: ProfileContent) -> Bundle:
    profile = content
    repo = InMemoryConversationRepository()
    tenant, session, version = uuid4(), uuid4(), uuid4()
    repo.add_tenant(TenantRecord(tenant, "grupo_sazon", "Grupo Sazón"))
    app = await repo.upsert_application(
        tenant, NewApplication("mine", "Lucía", "", "València", "repartidores")
    )
    ctx = RunContext(
        "conversador",
        tenant,
        "grupo_sazon",
        "tenant",
        "candidate",
        session,
        app.id,
        version,
        frozenset(profile.grants.conversador),
        profile.autonomy.model_dump(),
    )
    await repo.create_session(tenant, session, app.id, version, "web", ctx, "fixed")
    return Bundle(
        repo, ctx, profile, FixedClock(datetime(2026, 10, 22, tzinfo=UTC)), InMemoryTraceSink()
    )


def gateway(bundle: Bundle, registry: ConnectorRegistry) -> CapabilityGateway:
    return CapabilityGateway(
        bundle.repo,
        registry,
        bundle.sink,
        bundle.clock,
        DenyBooking(),
        PendingAssessment(),
        timeout_s=0.05,
    )


async def test_ungranted_is_blocked(bundle: Bundle) -> None:
    registry = StaticConnectorRegistry({})
    ctx = replace(bundle.ctx, granted=frozenset())
    assert await gateway(bundle, registry).call(
        ctx, bundle.profile, "openings_find", {"near": "Benimaclet"}
    ) == {"status": "not_allowed"}
    assert registry.calls == []
    assert len(bundle.sink.capability_calls) == 1
    assert bundle.sink.capability_calls[0].status == "blocked"


@pytest.mark.parametrize("schema", [2, 3])
@pytest.mark.parametrize("selected", [[], ["allowed", "disabled"]])
async def test_faq_tool_obeys_effective_offer_sources(bundle: Bundle, schema, selected) -> None:
    from screening.application.faq import FaqService, MemoryFaqStore
    from screening.domain.profile import OfferDefinition, Source, profile_for_offer
    from tests.application.test_faq_service import Embeddings

    profile = bundle.profile
    profile.schema_version = schema
    profile.brief.who = profile.brief.fit = ""
    profile.sources = [
        Source(
            id=key,
            name=key,
            kind="faq",
            mode="search",
            text="Respuesta " + key,
            in_context=key != "disabled",
        )
        for key in ["other", "allowed", "disabled"]
    ]
    profile.offers = [
        OfferDefinition(key="a", title="A", fields_mode="custom", source_ids=selected)
    ]
    faq = FaqService(Embeddings(), MemoryFaqStore())
    await faq.index(bundle.ctx, profile)
    service = CapabilityGateway(
        bundle.repo,
        StaticConnectorRegistry({}),
        bundle.sink,
        bundle.clock,
        DenyBooking(),
        PendingAssessment(),
    )
    service.faq = faq
    result = await service.call(
        bundle.ctx, profile_for_offer(profile, "a"), "faq_search", {"query": "Pregunta"}
    )
    expected = set(selected) - ({"disabled"} if schema == 3 else set())
    assert {row["source_id"] for row in result["fragments"]} == expected


async def test_dial_zero_blocks_writes_not_slot_reads(bundle: Bundle) -> None:
    registry = StaticConnectorRegistry({"list_slots": {"slots": []}})
    ctx = replace(bundle.ctx, autonomy={"handoff": 0, "schedule": 0})
    service = gateway(bundle, registry)
    assert await service.call(ctx, bundle.profile, "handoff_request", {"reason": "test"}) == {
        "status": "not_allowed"
    }
    assert await service.call(ctx, bundle.profile, "interview_slots", {"site_id": "one"}) == {
        "slots": []
    }
    assert [call.status for call in bundle.sink.capability_calls] == ["blocked", "ok"]


async def test_booking_needs_applied_qualification(bundle: Bundle) -> None:
    registry = StaticConnectorRegistry({})
    result = await gateway(bundle, registry).call(
        bundle.ctx, bundle.profile, "interview_book", {"slot_id": "one"}
    )
    assert result == {"status": "not_allowed"}
    assert registry.calls == []
    assert bundle.sink.capability_calls[0].status == "blocked"


async def test_handoff_platform_effects(bundle: Bundle) -> None:
    result = await gateway(bundle, StaticConnectorRegistry({})).call(
        bundle.ctx, bundle.profile, "handoff_request", {"reason": "needs person", "urgency": "high"}
    )
    assert result == {"status": "handed_off"}
    assert bundle.ctx.session_id is not None
    assert (
        await bundle.repo.get_session(bundle.ctx.tenant_id, bundle.ctx.session_id)
    ).status == "handed_off"
    assert bundle.repo.events[-1][2:] == (
        "handoff_requested",
        {"reason": "needs person", "urgency": "high"},
    )
    assert bundle.sink.capability_calls[0].connection_id == "platform"


async def test_followup_not_available_and_assessment_always_available(bundle: Bundle) -> None:
    service = gateway(bundle, StaticConnectorRegistry({}))
    ctx = replace(bundle.ctx, granted=frozenset({"followup.schedule"}), autonomy={"followup": 1})
    assert await service.call(
        ctx, bundle.profile, "followup_schedule", {"at_local": "2026-10-23T10:00", "reason": "test"}
    ) == {"status": "not_available_in_channel"}
    result = await service.call(
        replace(ctx, granted=frozenset()), bundle.profile, "screening_complete", {}
    )
    assert result["verdict"] == "review"
    assert result["next_step"] == "explain_review"
    assert len(bundle.sink.capability_calls) == 2


async def test_missing_backing_connection_is_unavailable(bundle: Bundle) -> None:
    bundle.profile.connections = []
    result = await gateway(bundle, StaticConnectorRegistry({})).call(
        bundle.ctx, bundle.profile, "openings_find", {"near": "Benimaclet"}
    )
    assert result == {"status": "unavailable"}
    assert bundle.sink.capability_calls[0].status == "error"


async def test_application_identifier_cannot_be_supplied_by_model(bundle: Bundle) -> None:
    registry = StaticConnectorRegistry({"get_application": {"candidate_name": "Lucía"}})
    result = await gateway(bundle, registry).call(
        bundle.ctx, bundle.profile, "application_read_mine", {"external_id": "other"}
    )
    assert result["candidate_name"] == "Lucía"
    assert registry.calls == [("get_application", {"external_id": "mine"})]
    assert bundle.sink.capability_calls[0].connection_id == "ats"


@pytest.mark.parametrize("failure", ["timeout", "exception"])
async def test_failures_are_bounded_and_traced(bundle: Bundle, failure: str) -> None:
    registry: ConnectorRegistry = (
        SlowConnectorRegistry(0.2)
        if failure == "timeout"
        else FailingConnectorRegistry(ValueError("simulated error"))
    )
    result = await gateway(bundle, registry).call(
        bundle.ctx, bundle.profile, "openings_find", {"near": "Benimaclet"}
    )
    assert result == {"status": "unavailable"}
    assert len(bundle.sink.capability_calls) == 1
    assert bundle.sink.capability_calls[0].status == "error"
    assert "error" in bundle.sink.capability_calls[0].response


async def test_large_trace_is_truncated(bundle: Bundle) -> None:
    response = {"status": "ok", "text": "a" * 10000}
    result = await gateway(bundle, StaticConnectorRegistry({"find_openings": response})).call(
        bundle.ctx, bundle.profile, "openings_find", {"near": "Benimaclet"}
    )
    assert result == response
    trace = bundle.sink.capability_calls[0]
    assert trace.response["truncated"] is True
    assert len(trace.response["preview"]) == 4000


async def test_successful_booking_uses_own_application_and_records_event(bundle: Bundle) -> None:
    class AllowedBooking(DenyBooking):
        async def can_book(self, ctx: RunContext) -> bool:
            return True

    registry = StaticConnectorRegistry(
        {"book_slot": {"status": "booked", "site_name": "Demo", "starts_at_local": "tomorrow"}}
    )
    service = gateway(bundle, registry)
    service.booking = AllowedBooking()
    assert (await service.call(bundle.ctx, bundle.profile, "interview_book", {"slot_id": "one"}))[
        "status"
    ] == "booked"
    assert registry.calls[0][1] == {"slot_id": "one", "application_external_id": "mine"}
    assert bundle.repo.events[-1][2] == "interview_booked"


async def test_real_policy_blocks_then_books_and_completes(bundle: Bundle) -> None:
    from adapters.fakes.decisions import InMemoryDecisionRepository
    from adapters.fakes.jobs import InMemoryJobQueue
    from screening.application.assessment import DecisionBookingPolicy

    decisions = InMemoryDecisionRepository()
    registry = StaticConnectorRegistry(
        {"book_slot": {"status": "booked", "site_name": "Demo", "starts_at_local": "mañana"}}
    )
    service = gateway(bundle, registry)
    service.booking = DecisionBookingPolicy(decisions, bundle.repo, InMemoryJobQueue())
    args = {"slot_id": "one"}
    assert (await service.call(bundle.ctx, bundle.profile, "interview_book", args))[
        "status"
    ] == "not_allowed"
    await decisions.create_decision(
        bundle.ctx.tenant_id,
        bundle.ctx.session_id,
        "qualify",
        "applied",
        2,
        "agent",
        None,
        "Cumple",
        bundle.clock.now(),
    )
    assert (await service.call(bundle.ctx, bundle.profile, "interview_book", args))[
        "status"
    ] == "booked"
    assert (
        await bundle.repo.get_session(bundle.ctx.tenant_id, bundle.ctx.session_id)
    ).status == "completed"
