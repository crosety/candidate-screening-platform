from uuid import UUID

import pytest
from django.test import Client

from tests.api.test_conversations_api import conversation_client as conversation_client
from web.accounts.models import User
from worker.loop import run_once

pytestmark = pytest.mark.django_db(transaction=True)


def test_evals_api_worker_and_gate(conversation_client: tuple[Client, UUID]) -> None:
    client, tenant = conversation_client
    assert len(client.get(f"/api/v1/tenants/{tenant}/personas").json()) == 6
    response = client.post(
        f"/api/v1/tenants/{tenant}/evals",
        {
            "persona_keys": ["lucia_carne_a2"],
            "compare_published": False,
            "max_cost_usd": 1,
            "offer_key": "repartidores",
        },
        content_type="application/json",
    )
    assert response.status_code == 200 and response.json()["status"] == "queued"
    assert run_once("eval-test")
    detail = client.get(f"/api/v1/tenants/{tenant}/evals/{response.json()['id']}").json()
    assert detail["status"] == "done" and detail["cases"][0]["verdict"] == "review"
    assert len(client.get(f"/api/v1/tenants/{tenant}/evals").json()) == 1
    gate = client.get(f"/api/v1/tenants/{tenant}/draft/gate").json()
    assert not gate["required"] and not gate["passed"]
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert client.get(f"/api/v1/tenants/{tenant}/evals").status_code == 403
