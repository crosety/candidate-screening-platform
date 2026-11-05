from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client

from web.accounts.models import User
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def profile_client(client: Client) -> tuple[Client, str]:
    call_command("seed_local")
    client.force_login(User.objects.get(email="fde@orbio.local"))
    return client, str(Tenant.objects.get(slug="grupo_sazon").id)


def get_draft(client: Client, tenant: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/tenants/{tenant}/draft")
    assert response.status_code == 200
    return response.json()


def put_draft(client: Client, tenant: str, draft: dict[str, Any]) -> Any:
    return client.put(
        f"/api/v1/tenants/{tenant}/draft",
        {"rev": draft["rev"], "content": draft["content"]},
        content_type="application/json",
    )


def publish(client: Client, tenant: str) -> Any:
    return client.post(
        f"/api/v1/tenants/{tenant}/publications",
        {"note": "Test", "percent": 100},
        content_type="application/json",
    )


def test_recruiter_cannot_read_profile(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert client.get(f"/api/v1/tenants/{tenant}/draft").status_code == 403


def test_stale_rev_conflict(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    draft = get_draft(client, tenant)
    assert put_draft(client, tenant, draft).status_code == 200
    assert put_draft(client, tenant, draft).status_code == 409


def test_publish_medium_change(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    draft = get_draft(client, tenant)
    draft["content"]["brief"]["fit"] += " Una frase nueva."
    assert put_draft(client, tenant, draft).status_code == 200
    response = publish(client, tenant)
    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert response.json()["version"] == "1.1"


def test_high_change_approval(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    draft = get_draft(client, tenant)
    draft["content"]["autonomy"]["qualify"] = 3
    assert put_draft(client, tenant, draft).status_code == 200
    response = publish(client, tenant)
    assert response.status_code == 200
    assert response.json()["state"] == "pending_approval"
    path = f"/api/v1/tenants/{tenant}/publications/{response.json()['id']}/approvals"
    assert (
        client.post(path, {"kind": "second_fde"}, content_type="application/json").status_code
        == 400
    )
    client.force_login(User.objects.get(email="fde2@orbio.local"))
    approved = client.post(path, {"kind": "second_fde"}, content_type="application/json")
    assert approved.status_code == 200
    assert approved.json()["state"] == "active"


def test_rollback_creates_version(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    original = client.get(f"/api/v1/tenants/{tenant}/versions").json()[0]
    draft = get_draft(client, tenant)
    original_content = draft["content"]
    draft["content"] = {**draft["content"], "operating_model": "Otro modelo."}
    assert put_draft(client, tenant, draft).status_code == 200
    assert publish(client, tenant).status_code == 200
    restored = client.post(
        f"/api/v1/tenants/{tenant}/versions/{original['id']}/rollback",
        {"note": "Volver"},
        content_type="application/json",
    )
    assert restored.status_code == 200
    assert restored.json()["version"] == "1.2"
    assert get_draft(client, tenant)["content"] == original_content


def test_invalid_profile_is_400(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    draft = get_draft(client, tenant)
    draft["content"]["autonomy"]["reject"] = 3
    assert put_draft(client, tenant, draft).status_code == 400


def test_other_tenant_returns_404(profile_client: tuple[Client, str]) -> None:
    client, _ = profile_client
    client.force_login(User.objects.get(email="fde2@orbio.local"))
    other = Tenant.objects.get(slug="logistica")
    assert client.get(f"/api/v1/tenants/{other.id}/draft").status_code == 404


def test_tenant_listing(profile_client: tuple[Client, str]) -> None:
    client, _ = profile_client
    response = client.get("/api/v1/tenants")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert {t["active_version"] for t in response.json()} == {"1.0"}


def test_pending_is_shared_between_independent_clients(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    draft = get_draft(client, tenant)
    draft["content"]["eval_threshold"] = 70
    assert put_draft(client, tenant, draft).status_code == 200
    row = publish(client, tenant).json()
    other = Client()
    other.force_login(User.objects.get(email="fde2@orbio.local"))
    pending = other.get(f"/api/v1/tenants/{tenant}/publications").json()
    assert [p["id"] for p in pending] == [row["id"]]
    response = other.post(
        f"/api/v1/tenants/{tenant}/publications/{row['id']}/approvals",
        {"kind": "second_fde"},
        content_type="application/json",
    )
    assert response.status_code == 200 and response.json()["state"] == "active"
    assert client.get(f"/api/v1/tenants/{tenant}/publications").json() == []


def test_pending_can_be_cancelled_and_not_rolled_back(profile_client: tuple[Client, str]) -> None:
    client, tenant = profile_client
    draft = get_draft(client, tenant)
    draft["content"]["eval_threshold"] = 70
    put_draft(client, tenant, draft)
    row = publish(client, tenant).json()
    version = client.get(f"/api/v1/tenants/{tenant}/versions").json()[0]
    assert not version["can_rollback"]
    assert (
        client.post(
            f"/api/v1/tenants/{tenant}/versions/{version['id']}/rollback",
            {"note": "Bypass"},
            content_type="application/json",
        ).status_code
        == 400
    )
    assert (
        client.post(f"/api/v1/tenants/{tenant}/publications/{row['id']}/cancel").status_code == 200
    )
    assert client.get(f"/api/v1/tenants/{tenant}/publications").json() == []
