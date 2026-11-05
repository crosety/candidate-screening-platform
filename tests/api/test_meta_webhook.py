import hashlib
import hmac
import json
from typing import Any
from uuid import uuid4

import pytest
from django.test import Client

from adapters.channels.meta import MetaSender
from web.conversations.models import ChannelInbound
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def webhook(monkeypatch: pytest.MonkeyPatch, client: Client) -> tuple[Client, str]:
    tenant = Tenant.objects.create(slug="meta_test", name="Meta Test")
    sender = MetaSender(
        phone_number_id="1234",
        business_account_id="5678",
        graph_version="v99.0",
        access_token="fake-token",
        app_secret="fake-secret",
        verify_token="fake-verify",
    )
    monkeypatch.setattr("web.conversations.meta_webhook.sender_config", lambda _: sender)
    return client, f"/api/v1/webhooks/whatsapp/{tenant.id}"


def signed_post(client: Client, url: str, data: dict[str, Any]) -> Any:
    body = json.dumps(data).encode()
    signature = "sha256=" + hmac.new(b"fake-secret", body, hashlib.sha256).hexdigest()
    return client.post(
        url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature
    )


def test_challenge_and_signature_before_parsing(webhook: tuple[Client, str]) -> None:
    client, url = webhook
    response = client.get(
        url, {"hub.mode": "subscribe", "hub.verify_token": "fake-verify", "hub.challenge": "proof"}
    )
    assert response.status_code == 200 and response.content == b"proof"
    malicious = client.get(
        url,
        {
            "hub.mode": "subscribe",
            "hub.verify_token": "fake-verify",
            "hub.challenge": "<script>alert(1)</script>",
        },
    )
    assert malicious["Content-Type"].startswith("text/plain")
    assert malicious["X-Content-Type-Options"] == "nosniff"
    invalid = client.get(url, {"hub.verify_token": "wrong", "hub.challenge": "<script>"})
    assert invalid.content == b""
    assert client.get(url, {"hub.verify_token": "wrong"}).status_code == 403
    assert client.post(url, b"not-json", content_type="application/json").status_code == 403
    assert Job.objects.count() == 0


def test_message_is_durable_and_idempotent_before_ack(webhook: tuple[Client, str]) -> None:
    client, url = webhook
    body = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "1234"},
                            "messages": [
                                {
                                    "id": "wamid.one",
                                    "from": "34600000000",
                                    "type": "text",
                                    "timestamp": "1792735200",
                                    "text": {"body": "Hola"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    assert signed_post(client, url, body).status_code == 200
    assert signed_post(client, url, body).status_code == 200
    assert Job.objects.count() == 1
    job = Job.objects.get()
    assert job.status == "queued" and job.kind == "channel_inbound"
    assert set(job.payload) == {"inbound_id"}
    with tenant_context(job.tenant_id):
        assert (
            ChannelInbound.objects.get(pk=job.payload["inbound_id"]).payload["provider_message_id"]
            == "wamid.one"
        )
    assert job.max_attempts == 1
    body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "other"
    assert signed_post(client, url, body).status_code == 400
    assert Job.objects.count() == 1


def test_unconfigured_webhook_is_closed(client: Client) -> None:
    assert client.get(f"/api/v1/webhooks/whatsapp/{uuid4()}").status_code == 403
