from uuid import UUID, uuid4

import pytest

from adapters.fakes.conversations import InMemoryConversationRepository
from adapters.fakes.decisions import InMemoryDecisionRepository
from adapters.fakes.jobs import InMemoryJobQueue
from adapters.persistence.decisions import DjangoDecisionRepository
from screening.application.ports import DecisionRepository, ResultRecord
from screening.domain.errors import Conflict, NotFound
from tests.contracts.test_conversation_repository import (
    Repository,
    create_session,
)
from tests.contracts.test_conversation_repository import (
    _close_thread_connections as _close_thread_connections,
)
from tests.contracts.test_conversation_repository import (
    repository as repository,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


@pytest.fixture
def decisions(repository: Repository) -> DecisionRepository:
    if isinstance(repository[0], InMemoryConversationRepository):
        return InMemoryDecisionRepository()
    return DjangoDecisionRepository()


async def setup(repository: Repository) -> tuple[UUID, UUID]:
    return repository[1], await create_session(repository)


async def test_result_is_unique_and_roundtrips_reason(
    decisions: DecisionRepository,
    repository: Repository,
) -> None:
    tenant, session = await setup(repository)
    row = ResultRecord(
        session, "review", {"name": {"value": "Lucía"}}, [], "Resumen", "Motivo", "test"
    )
    assert await decisions.get_result(tenant, session) is None
    await decisions.save_result(tenant, row)
    assert await decisions.get_result(tenant, session) == row
    with pytest.raises(Conflict):
        await decisions.save_result(tenant, row)
    assert await decisions.get_result(uuid4(), session) is None


async def test_outbox_deduplicates_and_tracks_attempts(
    decisions: DecisionRepository,
    repository: Repository,
) -> None:
    tenant, session = await setup(repository)
    first = await decisions.create_outbox(tenant, session, "ats.write_result", {"test": 1}, "same")
    second = await decisions.create_outbox(tenant, session, "ats.write_result", {"test": 2}, "same")
    assert first == second
    await decisions.mark_outbox(tenant, first.id, "sent")
    updated = await decisions.get_outbox(tenant, first.id)
    assert updated.status == "sent" and updated.attempts == 1
    with pytest.raises(NotFound):
        await decisions.get_outbox(uuid4(), first.id)


async def test_only_applied_qualification_allows_booking(
    decisions: DecisionRepository,
    repository: Repository,
) -> None:
    tenant, session = await setup(repository)
    assert not await decisions.has_applied_qualify(tenant, session)
    row = await decisions.create_decision(
        tenant, session, "qualify", "proposed", 1, "agent", None, "Motivo", None
    )
    assert not await decisions.has_applied_qualify(tenant, session)
    assert await decisions.list_proposed(tenant) == [row]
    await decisions.set_decision_status(tenant, row.id, "applied", None, None)
    assert await decisions.has_applied_qualify(tenant, session)
    assert await decisions.list_proposed(tenant) == []
    with pytest.raises(Conflict):
        await decisions.set_decision_status(tenant, row.id, "overridden", None, None)
    with pytest.raises(NotFound):
        await decisions.get_decision(uuid4(), row.id)


async def test_rejection_never_allows_booking(
    decisions: DecisionRepository,
    repository: Repository,
) -> None:
    tenant, session = await setup(repository)
    await decisions.create_decision(
        tenant, session, "reject", "applied", 2, "agent", None, "", None
    )
    assert not await decisions.has_applied_qualify(tenant, session)


async def test_memory_queue_deduplicates_and_copies_payload() -> None:
    queue = InMemoryJobQueue()
    data = {"id": "one"}
    await queue.enqueue("outbox_dispatch", data, None, "same", 3)
    data["id"] = "changed"
    await queue.enqueue("outbox_dispatch", data, None, "same", 3)
    assert len(queue.jobs) == 1
    assert queue.jobs[0]["payload"] == {"id": "one"}
    assert queue.jobs[0]["max_attempts"] == 3
