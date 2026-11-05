import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from adapters.channels.meta import MetaSender, MetaWhatsAppAdapter, normalize_meta, valid_signature
from adapters.fakes.clock import FixedClock
from screening.domain.channels import ChannelTemplate, OutboundMessage
from screening.domain.errors import NotAllowed


@pytest.fixture
def sender() -> MetaSender:
    return MetaSender(
        phone_number_id="1234",
        business_account_id="5678",
        graph_version="v99.0",
        access_token="fake-token",
        app_secret="fake-secret",
        verify_token="fake-verify",
    )


def test_signature_uses_original_bytes_and_rejects_malformed_header() -> None:
    body = b'{"entry": []}'
    signature = "sha256=" + hmac.new(b"fake-secret", body, hashlib.sha256).hexdigest()
    assert valid_signature(body, signature, "fake-secret")
    assert not valid_signature(body + b" ", signature, "fake-secret")
    assert not valid_signature(body, "sha256=ñ", "fake-secret")
    assert not valid_signature(body, signature, "")


@pytest.mark.asyncio
async def test_text_send_means_sent_not_delivered(sender: MetaSender) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})

    adapter = MetaWhatsAppAdapter(
        sender,
        FixedClock(datetime(2026, 10, 23, 10, tzinfo=UTC)),
        httpx.MockTransport(respond),
        enabled=True,
    )
    receipt = await adapter.send(
        OutboundMessage(uuid4(), "whatsapp", "+34600000000", "Hola", "one")
    )
    assert receipt.status == "sent" and not receipt.simulated
    assert str(requests[0].url) == "https://graph.facebook.com/v99.0/1234/messages"
    assert json.loads(requests[0].content)["text"]["preview_url"] is False


@pytest.mark.asyncio
async def test_unapproved_template_never_posts(sender: MetaSender) -> None:
    methods: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200, json={"data": [{"name": "hello", "language": "es", "status": "PENDING"}]}
        )

    adapter = MetaWhatsAppAdapter(
        sender,
        FixedClock(datetime(2026, 10, 23, 10, tzinfo=UTC)),
        httpx.MockTransport(respond),
        enabled=True,
    )
    template = ChannelTemplate("hello", "es", "followup", approved=True)
    with pytest.raises(NotAllowed, match="no está aprobada"):
        await adapter.send(
            OutboundMessage(uuid4(), "whatsapp", "34600000000", "Hola", "one", template=template)
        )
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_external_send_is_disabled_by_default(sender: MetaSender) -> None:
    adapter = MetaWhatsAppAdapter(sender, FixedClock(datetime(2026, 10, 23, 10, tzinfo=UTC)))
    with pytest.raises(NotAllowed, match="desactivados"):
        await adapter.send(OutboundMessage(uuid4(), "whatsapp", "34600000000", "Hola", "one"))


def test_normalized_input_does_not_send_candidate_token_to_model() -> None:
    code = "a" * 40
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
                                    "id": "one",
                                    "from": "34600000000",
                                    "type": "text",
                                    "timestamp": "1792735200",
                                    "text": {"body": "CODE " + code},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    incoming, statuses = normalize_meta(body, uuid4(), "1234")
    assert not statuses and incoming[0].candidate_code == code
    assert code not in incoming[0].text
    with pytest.raises(NotAllowed):
        normalize_meta(body, uuid4(), "other")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_http_failure_is_not_delivery_or_pii_in_error(
    sender: MetaSender, status: int
) -> None:
    adapter = MetaWhatsAppAdapter(
        sender,
        FixedClock(datetime(2026, 10, 23, 10, tzinfo=UTC)),
        httpx.MockTransport(lambda _: httpx.Response(status, json={"error": "private-candidate"})),
        enabled=True,
    )
    with pytest.raises(NotAllowed) as error:
        await adapter.send(OutboundMessage(uuid4(), "whatsapp", "+34600000000", "Hola", "one"))
    assert str(status) in str(error.value)
    assert "private-candidate" not in str(error.value)


@pytest.mark.asyncio
async def test_http_timeout_does_not_report_sent(sender: MetaSender) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Timed out", request=request)

    adapter = MetaWhatsAppAdapter(
        sender,
        FixedClock(datetime(2026, 10, 23, 10, tzinfo=UTC)),
        httpx.MockTransport(timeout),
        enabled=True,
    )
    with pytest.raises(httpx.ReadTimeout):
        await adapter.send(OutboundMessage(uuid4(), "whatsapp", "+34600000000", "Hola", "one"))
