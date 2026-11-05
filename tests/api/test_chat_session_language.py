import json
import time
from typing import Any
from uuid import UUID, uuid4

import pytest
from django.core.management import call_command
from django.test import Client

from web.conversations.ingest_auth import signature, tenant_key
from web.conversations.models import Session
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)
PATH = "/api/v1/ingest/grupo_sazon/applications"
DATA = {
    "external_id": "test-language",
    "candidate_name": "Alex",
    "phone": "",
    "city": "València",
    "job_ref": "repartidores",
    "timezone": "Europe/Madrid",
}


def candidate_token(client: Client) -> str:
    body = json.dumps(DATA).encode()
    timestamp, nonce = str(int(time.time())), uuid4().hex
    response = client.post(
        PATH,
        body,
        content_type="application/json",
        HTTP_X_INGEST_TIMESTAMP=timestamp,
        HTTP_X_INGEST_NONCE=nonce,
        HTTP_X_INGEST_SIGNATURE=signature(
            tenant_key("test-ingest-token", "grupo_sazon"), timestamp, nonce, PATH, body
        ),
    )
    assert response.status_code == 200
    return str(response.json()["candidate_link"]).rsplit("/", 1)[1]


def open_chat(client: Client, token: str, **choice: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/chat/sessions", {"token": token, **choice}, content_type="application/json"
    )
    assert response.status_code == 200
    return dict(response.json())


def test_chat_session_reports_the_classified_candidate_language(
    client: Client, settings: Any
) -> None:
    settings.INGEST_TOKEN = "test-ingest-token"
    call_command("seed_local")
    tenant = Tenant.objects.get(slug="grupo_sazon").id
    token = candidate_token(client)

    opened = open_chat(client, token)
    assert opened["language"] == ""  # Unknown until a candidate message is classified.

    with tenant_context(tenant):
        Session.objects.filter(pk=UUID(opened["session_id"])).update(language="en")
    resumed = open_chat(client, token)
    assert resumed["session_id"] == opened["session_id"]
    assert resumed["language"] == "en"


def test_explicit_language_is_persisted_before_greeting_and_survives_observations(
    client: Client, settings: Any
) -> None:
    settings.INGEST_TOKEN = "test-ingest-token"
    call_command("seed_local")
    token = candidate_token(client)
    opened = open_chat(client, token, language="en", fallback_language="es")
    assert opened["language"] == "en"
    tenant = Tenant.objects.get(slug="grupo_sazon").id
    with tenant_context(tenant):
        Session.objects.filter(pk=opened["session_id"]).update(language="es")
    assert open_chat(client, token, fallback_language="es")["language"] == "en"
    assert open_chat(client, token, language="es")["language"] == "es"


def test_unknown_language_is_not_an_instruction_channel(client: Client) -> None:
    response = client.post(
        "/api/v1/chat/sessions",
        {"token": "invalid", "language": "ignore rules"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "Entrada no válida" in response.json()["detail"]
