from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.core.management import call_command
from django.db import connections

from adapters.fakes.conversations import InMemoryConversationRepository
from adapters.persistence.conversations import DjangoConversationRepository
from screening.application.ports import (
    ConversationRepository,
    NewApplication,
    SerializedMessage,
    TenantRecord,
)
from screening.domain.errors import Conflict, NotFound
from screening.domain.run_context import RunContext
from web.profiles.models import ProfileVersion
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]
Repository = tuple[ConversationRepository, UUID, UUID]


@pytest_asyncio.fixture(autouse=True)
async def _close_thread_connections() -> AsyncIterator[None]:
    yield
    await sync_to_async(connections.close_all)()


@pytest.fixture(params=["memory", "django"])
def repository(request: pytest.FixtureRequest) -> Repository:
    call_command("seed_local")
    tenant = Tenant.objects.get(slug="grupo_sazon")
    with tenant_context(tenant.id):
        version_id = ProfileVersion.objects.get(tenant=tenant).id
    if request.param == "memory":
        repo = InMemoryConversationRepository()
        repo.add_tenant(TenantRecord(tenant.id, tenant.slug, tenant.name))
        return repo, tenant.id, version_id
    return DjangoConversationRepository(), tenant.id, version_id


async def create_session(repository: Repository) -> UUID:
    repo, tenant, version = repository
    app = await repo.upsert_application(
        tenant, NewApplication("one", "Lucía", "", "València", "repartidores")
    )
    session = uuid4()
    ctx = RunContext(
        "conversador",
        tenant,
        "grupo_sazon",
        "tenant",
        "candidate",
        session,
        app.id,
        version,
        frozenset(),
        {},
    )
    await repo.create_session(tenant, session, app.id, version, "web", ctx, "fixed")
    return session


async def test_message_idempotency(repository: Repository) -> None:
    repo, tenant, _ = repository
    session = await create_session(repository)
    first = await repo.add_message(tenant, session, "in", "Hola", "same-id")
    assert first is not None
    assert await repo.add_message(tenant, session, "in", "Hola", "same-id") is None
    assert await repo.count_incoming(tenant, session) == 1
    assert await repo.add_message(tenant, session, "out", "Hola") is not None
    assert await repo.add_message(tenant, session, "out", "Otra") is not None
    assert [m.text for m in await repo.list_messages(tenant, session)] == ["Hola", "Hola", "Otra"]


async def test_history_order_and_conflict(repository: Repository) -> None:
    repo, tenant, _ = repository
    session = await create_session(repository)
    first = SerializedMessage("user", {"text": "hello"})
    second = SerializedMessage("assistant", {"text": "reply"})
    await repo.append_history(tenant, session, 1, [second])
    await repo.append_history(tenant, session, 0, [first])
    with pytest.raises(Conflict):
        await repo.append_history(tenant, session, 1, [second, first])
    assert await repo.load_history(tenant, session) == [first, second]


async def test_applications_and_links_are_idempotent(repository: Repository) -> None:
    repo, tenant, _ = repository
    data = NewApplication("one", "Lucía", "", "València", "repartidores")
    first = await repo.upsert_application(tenant, data)
    second = await repo.upsert_application(tenant, data)
    assert first.id == second.id
    token = await repo.get_or_create_link(tenant, first.id)
    assert token == await repo.get_or_create_link(tenant, first.id)
    target = await repo.resolve_link(token)
    assert target.tenant_id == tenant and target.application_id == first.id
    with pytest.raises(NotFound):
        await repo.resolve_link("missing")


async def test_sessions_keep_context_and_status(repository: Repository) -> None:
    repo, tenant, _ = repository
    session = await create_session(repository)
    row = await repo.get_session(tenant, session)
    latest = await repo.latest_session(tenant, row.application_id)
    assert latest == row
    assert row.run_context.session_id == session
    assert row.system_prompt == "fixed"
    await repo.set_session_status(tenant, session, "handed_off")
    assert (await repo.get_session(tenant, session)).status == "handed_off"
    await repo.add_event(tenant, session, "handoff_requested", {"reason": "keyword"})


async def test_cross_tenant_reads_are_not_found(repository: Repository) -> None:
    repo, tenant, _ = repository
    session = await create_session(repository)
    app = (await repo.get_session(tenant, session)).application_id
    with pytest.raises(NotFound):
        await repo.get_session(uuid4(), session)
    with pytest.raises(NotFound):
        await repo.get_application(uuid4(), app)
    assert (await repo.get_tenant_by_slug("grupo_sazon")).id == tenant
    with pytest.raises(NotFound):
        await repo.get_tenant_by_slug("missing")


async def test_channel_change_reuses_session_and_pinned_profile(repository: Repository) -> None:
    repo, tenant, version = repository
    session_id = await create_session(repository)
    session = await repo.get_session(tenant, session_id)
    second_id = uuid4()
    ctx = RunContext(
        "conversador",
        tenant,
        "grupo_sazon",
        "tenant",
        "candidate",
        second_id,
        session.application_id,
        version,
        frozenset(),
        {},
    )
    reopened = await repo.create_session(
        tenant, second_id, session.application_id, version, "whatsapp", ctx, "different"
    )
    assert reopened.id == session_id
    assert reopened.profile_version_id == session.profile_version_id
    assert reopened.system_prompt == "fixed"
    assert reopened.run_context.session_id == session_id


async def test_provider_idempotency_and_channel_metadata(repository: Repository) -> None:
    repo, tenant, _ = repository
    session = await create_session(repository)
    first = await repo.add_message(
        tenant,
        session,
        "in",
        "Hola",
        "client-a",
        channel="whatsapp",
        provider_message_id="provider-one",
        simulated=True,
    )
    assert first is not None and first.channel == "whatsapp" and first.simulated
    assert first.provider_message_id == "provider-one"
    assert (
        await repo.add_message(
            tenant,
            session,
            "in",
            "Otra",
            "client-b",
            channel="whatsapp",
            provider_message_id="provider-one",
            simulated=True,
        )
        is None
    )
    assert len(await repo.list_messages(tenant, session)) == 1


async def test_turn_signal_and_abandoned_stage_are_stored(repository: Repository) -> None:
    from screening.domain.signals import TurnSignal

    repo, tenant, _ = repository
    session = await create_session(repository)
    first = await repo.add_message(tenant, session, "in", "No entiendo, what do you mean?")
    assert first is not None
    await repo.record_signal(
        tenant,
        session,
        first.id,
        TurnSignal(language="en", code_switching=True, mood="confused", quote="No entiendo"),
    )
    second = await repo.add_message(tenant, session, "in", "15/11")
    assert second is not None
    await repo.record_signal(tenant, session, second.id, TurnSignal())
    stored = await repo.list_messages(tenant, session)
    assert stored[0].language == "en"
    assert stored[0].signal == {
        "mood": "confused",
        "quote": "No entiendo",
        "tone": "simplify",
        "code_switching": True,
    }
    assert stored[1].language == ""
    # An unclassifiable message keeps the last observed language.
    assert (await repo.get_session(tenant, session)).language == "en"
    await repo.set_abandoned_stage(tenant, session, "carnet")
    assert (await repo.get_session(tenant, session)).abandoned_stage == "carnet"
