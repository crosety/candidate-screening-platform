import hashlib
import json
import time
from typing import Any
from uuid import UUID, uuid4

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.test import Client

from screening.application.ports import LlmUsage
from screening.platform.container import get_container
from web.accounts.models import User
from web.conversations.ingest_auth import signature, tenant_key
from web.conversations.models import Application, CandidateLink
from web.profiles.models import ProfileVersion, Publication
from web.simulated.models import AtsRecord
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)
DATA = {
    "external_id": "test-lucia",
    "candidate_name": "Lucía",
    "phone": "",
    "city": "València",
    "job_ref": "repartidores",
    "timezone": "Europe/Madrid",
}


@pytest.fixture
def conversation_client(client: Client, settings: Any) -> tuple[Client, UUID]:
    settings.INGEST_TOKEN = "test-ingest-token"
    call_command("seed_local")
    client.force_login(User.objects.get(email="fde@orbio.local"))
    return client, Tenant.objects.get(slug="grupo_sazon").id


def ingest(client: Client) -> dict[str, Any]:
    response = signed_ingest(client, DATA)
    assert response.status_code == 200
    return response.json()


def signed_ingest(client: Client, payload: dict[str, Any]) -> Any:
    path = "/api/v1/ingest/grupo_sazon/applications"
    body = json.dumps(payload).encode()
    timestamp, nonce = str(int(time.time())), uuid4().hex
    response = client.post(
        path,
        body,
        content_type="application/json",
        HTTP_X_INGEST_TIMESTAMP=timestamp,
        HTTP_X_INGEST_NONCE=nonce,
        HTTP_X_INGEST_SIGNATURE=signature(
            tenant_key("test-ingest-token", "grupo_sazon"), timestamp, nonce, path, body
        ),
    )
    return response


def open_session(client: Client) -> str:
    link = ingest(client)["candidate_link"]
    response = client.post(
        "/api/v1/chat/sessions", {"token": link.rsplit("/", 1)[1]}, content_type="application/json"
    )
    assert response.status_code == 200
    return response.json()["session_id"]


def test_ingest_requires_token(conversation_client: tuple[Client, UUID]) -> None:
    client, _ = conversation_client
    assert (
        client.post(
            "/api/v1/ingest/grupo_sazon/applications", DATA, content_type="application/json"
        ).status_code
        == 401
    )


def test_ingest_and_open_are_idempotent(conversation_client: tuple[Client, UUID]) -> None:
    client, _ = conversation_client
    assert ingest(client) == ingest(client)
    assert open_session(client) == open_session(client)


def test_unknown_chat_token_is_404(conversation_client: tuple[Client, UUID]) -> None:
    client, _ = conversation_client
    response = client.post(
        "/api/v1/chat/sessions", {"token": "false"}, content_type="application/json"
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Enlace no válido"


def test_recruiter_cannot_read_sessions(conversation_client: tuple[Client, UUID]) -> None:
    client, tenant = conversation_client
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert client.get(f"/api/v1/tenants/{tenant}/sessions").status_code == 403


def test_session_detail_orders_messages_and_sums_cost(
    conversation_client: tuple[Client, UUID],
) -> None:
    client, tenant = conversation_client
    session = UUID(open_session(client))
    container = get_container()
    async_to_sync(container.conversations.repo.add_message)(tenant, session, "in", "Hola", "one")
    async_to_sync(container.conversations.repo.add_message)(tenant, session, "out", "Bienvenida")
    async_to_sync(container.sink.record_llm_call)(
        LlmUsage(
            tenant, session, None, "conversador", "test", "low", 10, 20, 0, 0, 750, 100, "end_turn"
        )
    )
    response = client.get(f"/api/v1/tenants/{tenant}/sessions/{session}")
    assert response.status_code == 200
    data = response.json()
    assert [m["text"] for m in data["messages"]] == ["Hola", "Bienvenida"]
    assert data["version"]["version"] == "1.0"
    assert data["cost_usd_micros"] == 750
    assert data["result"] is None and data["decisions"] == []
    listing = client.get(f"/api/v1/tenants/{tenant}/sessions").json()
    assert listing[0]["messages"] == 2 and listing[0]["cost_usd_micros"] == 750


@pytest.mark.parametrize(
    "host",
    [
        "https://cloud.langfuse.com",
        "https://us.cloud.langfuse.com",
        "https://jp.cloud.langfuse.com",
    ],
)
def test_fde_session_links_to_its_exported_langfuse_identity(
    conversation_client: tuple[Client, UUID], settings: Any, host: str
) -> None:
    from web.jobs.models import Job

    client, tenant = conversation_client
    session = open_session(client)
    settings.LANGFUSE_PROJECT_ID = "metrics-project"
    settings.LANGFUSE_HOST = host
    # Historical metrics stay reachable even when new exports are paused.
    settings.LANGFUSE_ENABLED = False
    before = Job.objects.count()
    response = client.get(f"/api/v1/tenants/{tenant}/sessions/{session}")
    assert response.status_code == 200
    expected = hashlib.sha256(f"{tenant}:{session}".encode()).hexdigest()
    assert response.json()["langfuse_url"] == f"{host}/project/metrics-project/sessions/{expected}"
    assert Job.objects.count() == before
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert client.get(f"/api/v1/tenants/{tenant}/sessions/{session}").status_code == 403
    client.logout()
    assert client.get(f"/api/v1/tenants/{tenant}/sessions/{session}").status_code == 401


@pytest.mark.parametrize(
    ("host", "project"),
    [
        ("https://cloud.langfuse.com", ""),
        ("https://cloud.langfuse.com", "../other?secret=value"),
        ("https://cloud.langfuse.com", "x/y"),
        ("https://cloud.langfuse.com.evil.invalid", "project"),
        ("https://user:password@cloud.langfuse.com", "project"),
    ],
)
def test_session_omits_unconfigured_or_unsafe_metrics_links(
    conversation_client: tuple[Client, UUID], settings: Any, host: str, project: str
) -> None:
    client, tenant = conversation_client
    session = open_session(client)
    settings.LANGFUSE_HOST = host
    settings.LANGFUSE_PROJECT_ID = project
    response = client.get(f"/api/v1/tenants/{tenant}/sessions/{session}")
    assert response.status_code == 200
    assert response.json()["langfuse_url"] is None


def test_erased_application_has_no_metrics_link(
    conversation_client: tuple[Client, UUID], settings: Any
) -> None:
    from django.utils import timezone

    client, tenant = conversation_client
    session = open_session(client)
    settings.LANGFUSE_HOST = "https://cloud.langfuse.com"
    settings.LANGFUSE_PROJECT_ID = "metrics-project"
    with tenant_context(tenant):
        Application.objects.filter(tenant_id=tenant, external_id=DATA["external_id"]).update(
            erased_at=timezone.now()
        )
    assert client.get(f"/api/v1/tenants/{tenant}/sessions/{session}").json()["langfuse_url"] is None


def test_ats_create_and_candidate_link(conversation_client: tuple[Client, UUID]) -> None:
    client, tenant = conversation_client
    payload = {k: DATA[k] for k in ("candidate_name", "phone", "city", "job_ref")}
    first = client.post(
        f"/api/v1/tenants/{tenant}/simulated/ats", payload, content_type="application/json"
    )
    assert first.status_code == 200
    assert first.json()["external_id"] == "SIM-0001"
    rows = client.get(f"/api/v1/tenants/{tenant}/simulated/ats").json()
    assert rows[0]["candidate_link"] == first.json()["candidate_link"]
    assert rows[0]["stage"] == "Nuevo"
    second = client.post(
        f"/api/v1/tenants/{tenant}/simulated/ats", payload, content_type="application/json"
    )
    assert second.json()["external_id"] == "SIM-0002"
    opened = client.post(
        "/api/v1/chat/sessions",
        {"token": first.json()["candidate_link"].rsplit("/", 1)[1]},
        content_type="application/json",
    )
    assert opened.status_code == 200


@pytest.mark.parametrize(
    "status", ["paused", "closed", "draft", "missing", "unpublished", "partial"]
)
def test_ats_rechecks_publication_before_creating_a_simulation(
    conversation_client: tuple[Client, UUID], status: str
) -> None:
    client, tenant = conversation_client
    base = f"/api/v1/tenants/{tenant}"
    shown = client.get(base + "/offers").json()["offers"]
    assert next(o for o in shown if o["key"] == "repartidores")["status"] == "open"
    with tenant_context(tenant):
        before = (
            AtsRecord.objects.count(),
            Application.objects.count(),
            CandidateLink.objects.count(),
        )
        publication = Publication.objects.get(tenant_id=tenant, state="active", segment="")
        old = publication.version
        content = dict(old.content)
        content["offers"] = [
            {**item, "status": status if status in {"paused", "closed", "draft"} else "open"}
            for item in content["offers"]
        ]
        # Represent a publication changed after the browser loaded its selector.
        changed = ProfileVersion.objects.create(
            tenant_id=tenant,
            seq=2,
            version="1.1",
            content=content,
            content_hash="new-test-version",
            schema_version=old.schema_version,
            risk="low",
            published_by_id=old.published_by_id,
        )
        publication.version = changed
        if status == "unpublished":
            publication.state = "superseded"
        if status == "partial":
            publication.percent = 10
            publication.previous_version_id = old.id
        publication.save(update_fields=["version", "state", "percent", "previous_version_id"])
    payload = {k: DATA[k] for k in ("candidate_name", "phone", "city", "job_ref")}
    if status == "missing":
        payload["job_ref"] = "unknown-offer"
    response = client.post(base + "/simulated/ats", payload, content_type="application/json")
    assert response.status_code == 409
    assert "publicada" in response.json()["detail"]
    with tenant_context(tenant):
        assert (
            AtsRecord.objects.count(),
            Application.objects.count(),
            CandidateLink.objects.count(),
        ) == before


def test_ats_legacy_profile_still_opens_its_common_context(
    conversation_client: tuple[Client, UUID],
) -> None:
    client, _ = conversation_client
    tenant = Tenant.objects.get(slug="logistica").id
    payload = {k: DATA[k] for k in ("candidate_name", "phone", "city", "job_ref")}
    payload["job_ref"] = "ALMACEN-LEGACY"
    response = client.post(
        f"/api/v1/tenants/{tenant}/simulated/ats", payload, content_type="application/json"
    )
    assert response.status_code == 200
    opened = client.post(
        "/api/v1/chat/sessions",
        {"token": response.json()["candidate_link"].rsplit("/", 1)[1]},
        content_type="application/json",
    )
    assert opened.status_code == 200


def test_cross_tenant_session_is_not_found(conversation_client: tuple[Client, UUID]) -> None:
    client, _ = conversation_client
    session = open_session(client)
    other = Tenant.objects.get(slug="logistica")
    assert client.get(f"/api/v1/tenants/{other.id}/sessions/{session}").status_code == 404


def test_invalid_timezone_is_400(conversation_client: tuple[Client, UUID]) -> None:
    client, _ = conversation_client
    response = signed_ingest(client, {**DATA, "timezone": "not/a/timezone"})
    assert response.status_code == 400
