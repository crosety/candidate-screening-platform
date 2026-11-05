from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from adapters.channels.twilio import TwilioClient, TwilioConfig, valid_twilio_signature
from screening.domain.errors import NotAllowed


def config(**overrides: object) -> TwilioConfig:
    return TwilioConfig.model_validate(
        {
            "tenant_id": str(uuid4()),
            "account_sid": "AC" + "1" * 32,
            "auth_token": "synthetic-token-for-tests",
            "sender": "+14155238886",
            "public_base_url": "https://test.example",
            "participants": ["+34600000001"],
            "enabled": True,
            **overrides,
        }
    )


def test_signature_matches_twilio_published_vector() -> None:
    # Independent reference vector from Twilio Security documentation.
    params = {
        "CallSid": ["CA1234567890ABCDE"],
        "Caller": ["+14158675310"],
        "Digits": ["1234"],
        "From": ["+14158675310"],
        "To": ["+18005551212"],
    }
    url = "https://example.com/myapp.php?foo=1&bar=2"
    signature = "L/OH5YylLD5NRKLltdqwSvS0BnU="
    assert valid_twilio_signature(url, params, signature, "12345")
    assert not valid_twilio_signature(url + "x", params, signature, "12345")
    assert not valid_twilio_signature(url, params | {"FutureField": ["x"]}, signature, "12345")
    assert not valid_twilio_signature(url, params, "wrong", "12345")


@pytest.mark.parametrize(
    "url",
    [
        "http://test.example",
        "https://user:pass@test.example",
        "https://test.example/path",
        "https://test.example/?secret=x",
    ],
)
def test_config_rejects_unsafe_callback(url: str) -> None:
    with pytest.raises(ValidationError):
        config(public_base_url=url)


def test_temporary_authorization_requires_timezone_and_expires() -> None:
    with pytest.raises(ValidationError):
        config(expires_at="2026-10-27T12:00:00")
    assert config(expires_at=datetime.now(UTC) + timedelta(hours=1)).active
    assert not config(expires_at=datetime.now(UTC) - timedelta(seconds=1)).active
    assert not config(enabled=False).active


@pytest.mark.asyncio
async def test_expired_authorization_never_posts() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"sid": "SM" + "2" * 32})

    cfg = config(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(NotAllowed):
        await TwilioClient(cfg, httpx.MockTransport(handler)).send("+34600000001", "Test")
    assert not calls


@pytest.mark.asyncio
async def test_single_post_fixed_host_basic_auth_and_no_delivery_claim() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "api.twilio.com"
        data = parse_qs(request.content.decode())
        assert data["To"] == ["whatsapp:+34600000001"]
        assert data["Body"] == ["Mensaje de prueba"]
        assert data["StatusCallback"][0].endswith("/status")
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(201, json={"sid": "SM" + "2" * 32, "status": "queued"})

    client = TwilioClient(config(), httpx.MockTransport(handler))
    assert await client.send("+34600000001", "Mensaje de prueba") == "SM" + "2" * 32
    assert len(calls) == 1
    for phone, text in [("+34600000002", "Test"), ("+34600000001", "x" * 1601)]:
        with pytest.raises(NotAllowed):
            await client.send(phone, text)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 429, 500])
async def test_failure_never_retries_or_leaks_provider_body(status: int) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status, json={"secret": "private-body"}, headers={"Location": "https://bad.example"}
        )

    with pytest.raises(NotAllowed) as exc:
        await TwilioClient(config(), httpx.MockTransport(handler)).send("+34600000001", "Test")
    assert "private-body" not in str(exc.value) and len(calls) == 1
