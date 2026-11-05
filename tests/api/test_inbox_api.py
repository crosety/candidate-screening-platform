from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from screening.platform.container import get_container
from tests.api.test_conversations_api import conversation_client as conversation_client
from tests.api.test_conversations_api import open_session
from web.accounts.models import User
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


def test_recruiter_can_decide_and_cross_tenant_is_hidden(
    conversation_client: tuple[Client, UUID],
) -> None:
    client, tenant = conversation_client
    session = UUID(open_session(client))
    container = get_container()
    ctx = async_to_sync(container.conversations.repo.get_session)(tenant, session).run_context
    async_to_sync(container.assessment.complete)(ctx)
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    response = client.get(f"/api/v1/tenants/{tenant}/inbox")
    assert response.status_code == 200
    decision = response.json()[0]["decision_id"]
    assert (
        client.post(
            f"/api/v1/tenants/{tenant}/decisions/{decision}",
            {"action": "confirm"},
            content_type="application/json",
        ).status_code
        == 400
    )
    applied = client.post(
        f"/api/v1/tenants/{tenant}/decisions/{decision}",
        {"action": "reject", "reason": "Revisión humana"},
        content_type="application/json",
    )
    assert applied.status_code == 200 and applied.json()["status"] == "applied"
    other = Tenant.objects.get(slug="logistica").id
    assert client.get(f"/api/v1/tenants/{other}/inbox").status_code == 404
    client.force_login(User.objects.get(email="fde@orbio.local"))
    assert (
        client.post(
            f"/api/v1/tenants/{other}/decisions/{decision}",
            {"action": "reject", "reason": "x"},
            content_type="application/json",
        ).status_code
        == 404
    )
    detail = client.get(f"/api/v1/tenants/{tenant}/sessions/{session}").json()
    assert detail["result"]["verdict"] == "review"
    assert len(detail["decisions"]) == 2
