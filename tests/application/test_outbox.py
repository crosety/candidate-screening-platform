import pytest

from adapters.fakes.connectors import FailingConnectorRegistry, StaticConnectorRegistry
from screening.application.outbox import OutboxService
from tests.application.test_assessment import AssessmentBundle
from tests.application.test_assessment import assessment as assessment
from tests.application.test_assessment import bundle as bundle
from tests.application.test_assessment import content as content

pytestmark = pytest.mark.asyncio


async def test_success_is_idempotent(assessment: AssessmentBundle) -> None:
    await assessment.service.complete(assessment.ctx)
    registry = StaticConnectorRegistry({"write_result": {"status": "ok"}})
    service = OutboxService(
        assessment.decisions, assessment.conversation.repo, assessment.service.versions, registry
    )
    item = next(iter(assessment.decisions.outbox.values()))
    await service.dispatch(item.tenant_id, item.id)
    await service.dispatch(item.tenant_id, item.id)
    assert (await assessment.decisions.get_outbox(item.tenant_id, item.id)).status == "sent"
    assert len(registry.calls) == 1


async def test_failure_stays_pending_and_raises(assessment: AssessmentBundle) -> None:
    await assessment.service.complete(assessment.ctx)
    service = OutboxService(
        assessment.decisions,
        assessment.conversation.repo,
        assessment.service.versions,
        FailingConnectorRegistry(RuntimeError("ATS caído")),
    )
    item = next(iter(assessment.decisions.outbox.values()))
    with pytest.raises(RuntimeError, match="ATS caído"):
        await service.dispatch(item.tenant_id, item.id)
    assert (await assessment.decisions.get_outbox(item.tenant_id, item.id)).status == "pending"


async def test_no_write_permission_is_dead(assessment: AssessmentBundle) -> None:
    await assessment.service.complete(assessment.ctx)
    version = await assessment.service.versions.get(
        assessment.ctx.tenant_id, assessment.ctx.profile_version_id
    )
    next(c for c in version.content.connections if c.kind == "ats").permission = "read"
    service = OutboxService(
        assessment.decisions,
        assessment.conversation.repo,
        assessment.service.versions,
        StaticConnectorRegistry({}),
    )
    # The fake version store is the canonical version used by the service.
    assessment.conversation.profiles.repo.versions[version.id] = version
    item = next(iter(assessment.decisions.outbox.values()))
    await service.dispatch(item.tenant_id, item.id)
    assert (await assessment.decisions.get_outbox(item.tenant_id, item.id)).status == "dead"
