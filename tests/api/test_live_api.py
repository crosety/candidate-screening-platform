from uuid import UUID

import pytest
from django.test import Client

from tests.api.test_conversations_api import conversation_client as conversation_client
from web.jobs.models import Job
from worker.loop import run_once

pytestmark = pytest.mark.django_db(transaction=True)


def test_inspection_live_and_open_site(conversation_client: tuple[Client, UUID]) -> None:
    client, tenant = conversation_client
    path = f"/api/v1/tenants/{tenant}"
    response = client.get(f"{path}/connections/ats/inspect")
    assert response.status_code == 200 and "Etapas:" in response.json()["structure"][0]
    assert client.get(f"{path}/connections/missing/inspect").status_code == 404
    assert client.get(f"{path}/live").json() == []
    assert Job.objects.get().kind == "live_sync"
    assert client.get(f"{path}/live").status_code == 200 and Job.objects.count() == 1
    assert client.get(f"{path}/simulated/sites").json()
    response = client.post(
        f"{path}/simulated/open-site", {"external_id": "VLC-BMC"}, content_type="application/json"
    )
    assert response.status_code == 200 and Job.objects.count() == 2
    assert run_once("live-test")
    data = client.get(f"{path}/live").json()
    assert len(data) == 3
    assert any(w["code"] == "site_without_calendar" for row in data for w in row["warnings"])
    assert any(c["code"] == "W005" for c in client.get(f"{path}/draft/checks").json())
