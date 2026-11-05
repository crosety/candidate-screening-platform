import json
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from tools.prepare_voice_acceptance import prepare_cases

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def acceptance_client() -> Iterator[tuple[httpx.Client, UUID, UUID]]:
    call_command("seed_local", stdout=StringIO())
    # The real HTTP application and Postgres are exercised; no socket or provider is opened.
    with httpx.Client(
        transport=httpx.WSGITransport(app=get_wsgi_application()), base_url="http://testserver"
    ) as client:
        client.headers["X-CSRFToken"] = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        logged_in = client.post(
            "/api/v1/auth/login",
            json={"email": "fde@orbio.local", "password": "test-only-password"},
        )
        assert logged_in.status_code == 200
        client.headers["X-CSRFToken"] = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        tenant = next(
            row for row in client.get("/api/v1/tenants").json() if row["slug"] == "grupo_sazon"
        )
        versions = client.get(f"/api/v1/tenants/{tenant['id']}/versions").json()
        active = next(row for row in versions if row["active_percent"] == 100)
        yield client, UUID(tenant["id"]), UUID(active["id"])


def test_voice_preparation_uses_published_offer_without_starting_a_model(
    acceptance_client: tuple[httpx.Client, UUID, UUID], tmp_path: Path
) -> None:
    client, tenant, version = acceptance_client
    output = tmp_path / "private" / "cases.json"
    cases = prepare_cases(client, tenant, "repartidores", version, ["handoff", "normal"], output)
    assert json.loads(output.read_text()) == cases
    assert output.stat().st_mode & 0o777 == 0o600
    assert cases["handoff"]["session_id"] != cases["normal"]["session_id"]
    for case in cases.values():
        resumed = client.post("/api/v1/chat/sessions", json={"token": case["token"]}).json()
        assert resumed["is_test"] is True
        assert resumed["session_id"] == case["session_id"]
        detail = client.get(f"/api/v1/tenants/{tenant}/sessions/{case['session_id']}").json()
        assert detail["version"]["id"] == str(version)
        assert detail["session"]["channel"] == "web"
        assert detail["session"]["status"] == "active"
        assert detail["application"]["job_ref"] == "repartidores"
        assert detail["messages"] == []
        assert detail["llm_calls"] == []
        assert detail["capability_calls"] == []
        assert case["ready"] is True


def test_voice_preparation_never_repeats_a_batch_with_an_existing_receipt(
    acceptance_client: tuple[httpx.Client, UUID, UUID], tmp_path: Path
) -> None:
    client, tenant, version = acceptance_client
    output = tmp_path / "cases.json"
    first = prepare_cases(client, tenant, "repartidores", version, ["handoff"], output)
    before = client.get(f"/api/v1/tenants/{tenant}/simulated/ats").json()
    with pytest.raises(FileExistsError):
        prepare_cases(client, tenant, "repartidores", version, ["normal"], output)
    assert client.get(f"/api/v1/tenants/{tenant}/simulated/ats").json() == before
    assert json.loads(output.read_text()) == first


def test_voice_preparation_rejects_an_unpublished_version_before_any_write(
    acceptance_client: tuple[httpx.Client, UUID, UUID], tmp_path: Path
) -> None:
    client, tenant, _ = acceptance_client
    output = tmp_path / "cases.json"
    before = client.get(f"/api/v1/tenants/{tenant}/simulated/ats").json()
    with pytest.raises(ValueError, match="no está publicada al 100"):
        prepare_cases(client, tenant, "repartidores", uuid4(), ["normal"], output)
    assert not output.exists()
    assert client.get(f"/api/v1/tenants/{tenant}/simulated/ats").json() == before
