from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from adapters.persistence.copilot import DjangoCopilotMaterialReader
from tests.api.test_conversations_api import (
    conversation_client as conversation_client,
)
from tests.api.test_conversations_api import (
    open_session,
)
from web.accounts.models import User
from web.conversations.models import Message, Session
from web.decisions.models import Decision, Result
from web.evals.models import EvalCase, EvalRun
from web.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db(transaction=True)


def test_copilot_permissions_and_material(conversation_client: tuple[Client, UUID]) -> None:
    client, tenant = conversation_client
    session = UUID(open_session(client))
    with tenant_context(tenant):
        Session.objects.filter(pk=session).update(status="completed")
        Message.objects.create(tenant_id=tenant, session_id=session, direction="in", text="Prueba")
        Result.objects.create(
            tenant_id=tenant,
            session_id=session,
            verdict="review",
            summary="",
            evaluator_model="fake",
        )
        Decision.objects.create(
            tenant_id=tenant,
            session_id=session,
            decision_type="reject",
            status="overridden",
            autonomy_level=1,
            decided_by="user",
            reason="Turno corregido",
        )
        run = EvalRun.objects.create(
            tenant_id=tenant,
            draft_rev=1,
            max_cost_usd_micros=100,
            created_by=User.objects.get(email="fde@orbio.local"),
        )
        EvalCase.objects.create(
            tenant_id=tenant,
            run=run,
            persona_key="test",
            target="draft",
            score=40,
            judge={"comment": "Falta cierre"},
        )
        EvalCase.objects.create(
            tenant_id=tenant, run=run, persona_key="good", target="draft", score=90
        )
    items = async_to_sync(DjangoCopilotMaterialReader().recent)(tenant)
    assert len(items.sessions) == 1 and items.sessions[0]["verdict"] == "review"
    assert items.corrections[0]["decision_type"] == "reject"
    assert "reason" not in items.corrections[0]
    assert len(items.eval_cases) == 1 and items.eval_cases[0]["score"] == 40
    path = f"/api/v1/tenants/{tenant}/copilot/suggestions"
    assert client.post(path, {"section": "brief"}, content_type="application/json").json() == []
    assert client.post(path, {"section": "bad"}, content_type="application/json").status_code == 400
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert (
        client.post(path, {"section": "brief"}, content_type="application/json").status_code == 403
    )
