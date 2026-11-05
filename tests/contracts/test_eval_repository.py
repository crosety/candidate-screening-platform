from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db import connections

from adapters.fakes.evals import InMemoryEvalRepository
from adapters.persistence.evals import DjangoEvalRepository
from adapters.persistence.tracing import DjangoTraceSink
from screening.application.ports import EvalRepository, LlmUsage
from screening.domain.errors import NotFound
from web.accounts.models import User
from web.tenancy.models import Tenant

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


@sync_to_async
def identities():
    tenant = Tenant.objects.create(slug="eval_contract", name="Eval")
    user = User.objects.create_user(email="eval@test.local", password="test")
    return tenant.id, user.id


@pytest.mark.parametrize("mode", ["memory", "django"])
async def test_repository_contract(mode: str) -> None:
    tenant, user = await identities()
    repo: EvalRepository = InMemoryEvalRepository() if mode == "memory" else DjangoEvalRepository()
    try:
        run = await repo.create_run(tenant, 4, None, ["lucia"], False, 1000000, user)
        case = await repo.create_case(tenant, run.id, "lucia", "draft")
        other = await repo.create_case(tenant, run.id, "other", "draft")
        if isinstance(repo, InMemoryEvalRepository):
            repo.costs[case.id] = 30
            repo.costs[other.id] = 90
        else:
            sink = DjangoTraceSink()
            for target, cost in ((case.id, 10), (case.id, 20), (other.id, 90)):
                await sink.record_llm_call(
                    LlmUsage(
                        tenant, uuid4(), target, "juez", "test", "medium", 1, 1, 0, 0, cost, 1, ""
                    )
                )
        assert await repo.case_cost(tenant, case.id) == 30
        await repo.finish_case(
            tenant,
            case.id,
            [{"from": "agent", "text": "Hola"}],
            {"comment": "Bien"},
            "qualified",
            91,
            "done",
            30,
        )
        rows = await repo.list_cases(tenant, run.id)
        saved = next(row for row in rows if row.id == case.id)
        assert saved.verdict == "qualified" and saved.score == 91
        assert await repo.claim_run(tenant, run.id)
        assert not await repo.claim_run(tenant, run.id)
        await repo.set_run(tenant, run.id, status="done", score_draft=91)
        assert (await repo.get_run(tenant, run.id)).score_draft == 91
        with pytest.raises(ValueError):
            await repo.set_run(tenant, run.id, draft_rev=99)
        with pytest.raises(NotFound):
            await repo.get_run(uuid4(), run.id)
    finally:
        await sync_to_async(connections.close_all)()
