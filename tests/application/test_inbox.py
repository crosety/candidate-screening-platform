from uuid import uuid4

import pytest

from screening.application.inbox import InboxService
from screening.domain.errors import NotAllowed
from tests.application.test_assessment import AssessmentBundle
from tests.application.test_assessment import assessment as assessment
from tests.application.test_assessment import bundle as bundle
from tests.application.test_assessment import content as content

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def inbox(assessment: AssessmentBundle) -> InboxService:
    assessment.evaluator.outputs[0].verdict = "not_qualified"
    await assessment.service.complete(assessment.ctx)
    return InboxService(
        assessment.decisions,
        assessment.conversation.repo,
        assessment.service.versions,
        assessment.queue,
        assessment.service.clock,
    )


async def test_list_and_confirm(inbox: InboxService, assessment: AssessmentBundle) -> None:
    items = await inbox.list_pending(assessment.ctx.tenant_id)
    assert len(items) == 1 and items[0].candidate_name == "Lucía"
    result = await inbox.act(assessment.ctx.tenant_id, items[0].decision_id, "confirm", "", uuid4())
    assert result.status == "applied" and result.decided_by == "user"
    assert await inbox.list_pending(assessment.ctx.tenant_id) == []
    assert len(assessment.queue.jobs) == 1
    with pytest.raises(NotAllowed, match="ya no"):
        await inbox.act(assessment.ctx.tenant_id, result.id, "confirm", "", uuid4())


async def test_override_needs_reason(inbox: InboxService, assessment: AssessmentBundle) -> None:
    decision = (await inbox.list_pending(assessment.ctx.tenant_id))[0]
    with pytest.raises(NotAllowed, match="motivo"):
        await inbox.act(assessment.ctx.tenant_id, decision.decision_id, "qualify", "", uuid4())
    applied = await inbox.act(
        assessment.ctx.tenant_id, decision.decision_id, "qualify", "Documento verificado", uuid4()
    )
    assert applied.decision_type == "qualify" and applied.autonomy_level == 0
    original = await assessment.decisions.get_decision(
        assessment.ctx.tenant_id, decision.decision_id
    )
    assert original.status == "overridden"
    assert next(iter(assessment.decisions.outbox.values())).payload["verdict"] == "qualified"


async def test_review_cannot_be_confirmed(assessment: AssessmentBundle) -> None:
    assessment.evaluator.outputs[0].verdict = "review"
    await assessment.service.complete(assessment.ctx)
    service = InboxService(
        assessment.decisions,
        assessment.conversation.repo,
        assessment.service.versions,
        assessment.queue,
        assessment.service.clock,
    )
    decision = (await service.list_pending(assessment.ctx.tenant_id))[0]
    with pytest.raises(NotAllowed, match="Elige"):
        await service.act(assessment.ctx.tenant_id, decision.decision_id, "confirm", "", uuid4())


async def test_same_action_confirms_without_reason(
    inbox: InboxService, assessment: AssessmentBundle
) -> None:
    decision = (await inbox.list_pending(assessment.ctx.tenant_id))[0]
    applied = await inbox.act(assessment.ctx.tenant_id, decision.decision_id, "reject", "", uuid4())
    assert applied.id == decision.decision_id and applied.status == "applied"
