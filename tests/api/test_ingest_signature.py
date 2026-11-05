import json
import time
from uuid import UUID, uuid4

import pytest
from django.test import Client

from tests.api.test_conversations_api import DATA, conversation_client
from web.conversations.ingest_auth import signature, tenant_key

__all__ = ["conversation_client"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("alteration", ["body", "tenant", "old", "future", "unicode", "none"])
def test_ingest_signature_binds_tenant_body_time_and_nonce(
    conversation_client: tuple[Client, UUID], alteration: str
) -> None:
    client, _ = conversation_client
    body = json.dumps(DATA).encode()
    path = "/api/v1/ingest/grupo_sazon/applications"
    timestamp = str(int(time.time()) + {"old": -301, "future": 60}.get(alteration, 0))
    nonce = uuid4().hex
    key = tenant_key("test-ingest-token", "otro" if alteration == "tenant" else "grupo_sazon")
    sig = signature(key, timestamp, nonce, path, body)
    if alteration == "body":
        body = json.dumps({**DATA, "candidate_name": "Distinto"}).encode()
    if alteration == "unicode":
        sig = "é" * 64
    headers = {
        "HTTP_X_INGEST_TIMESTAMP": timestamp,
        "HTTP_X_INGEST_NONCE": nonce,
        "HTTP_X_INGEST_SIGNATURE": sig,
    }
    response = client.post(path, body, content_type="application/json", **headers)
    assert response.status_code == (200 if alteration == "none" else 401)
    if alteration == "none":
        assert (
            client.post(path, body, content_type="application/json", **headers).status_code == 409
        )
