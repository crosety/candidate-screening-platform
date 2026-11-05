import json
from datetime import timedelta
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

from adapters.channels.twilio_sms import SmsConfig, SmsOutcomeUnknown, TwilioSmsClient, segments
from screening.domain.errors import NotAllowed
from tests.api.test_telephone import callback, phone, reserve, setup, sign
from web.conversations import sms_links
from web.conversations.models import CandidateLink, SmsNotification, TelephoneAttempt
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

__all__ = ["phone", "setup"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def sms(setup, settings, tmp_path):
    s = setup
    config = SmsConfig(
        tenant_id=s.tenant,
        enabled=True,
        candidate_base_url="https://candidate.example.test",
        callback_base_url="https://candidate.example.test",
        expires_at=timezone.now() + timedelta(hours=2),
    )
    path = tmp_path / "sms.json"
    path.write_text(config.model_dump_json())
    settings.TWILIO_SMS_CONFIG_FILE = str(path)
    s.sms, s.sms_path = config, path
    return s


def invite(s, key="invitation:test"):
    link = CandidateLink.objects.get(application_id=s.app)
    return sms_links.reserve_sms(s.tenant, s.sid, link.id, "+34600000000", key)


def test_consent_scope_allowlist_and_no_duplicate(sms):
    s = sms
    body = dict(
        session_id=str(s.sid),
        token=s.token,
        request_id=str(uuid4()),
        phone="+34600000000",
        consent=False,
    )
    assert (
        s.client.post("/api/v1/chat/sms-link", body, content_type="application/json").status_code
        == 400
    )
    body["consent"] = True
    r = s.client.post("/api/v1/chat/sms-link", body, content_type="application/json")
    assert r.status_code == 200, r.content
    assert (
        s.client.post("/api/v1/chat/sms-link", body, content_type="application/json").json()["id"]
        == r.json()["id"]
    )
    body["request_id"] = str(uuid4())
    assert (
        s.client.post("/api/v1/chat/sms-link", body, content_type="application/json").status_code
        == 400
    )
    body["phone"] = "+34699999999"
    assert (
        s.client.post("/api/v1/chat/sms-link", body, content_type="application/json").status_code
        == 400
    )
    body["token"] = "invalid"
    assert s.client.post(
        "/api/v1/chat/sms-link", body, content_type="application/json"
    ).status_code in {400, 404}
    with tenant_context(s.tenant):
        assert SmsNotification.objects.count() == 1
        assert Job.objects.filter(kind="sms_link_send").count() == 1


def test_unknown_send_is_never_retried_even_after_expiry(sms, monkeypatch):
    s = sms
    row = invite(s)
    send = AsyncMock(side_effect=SmsOutcomeUnknown)
    monkeypatch.setattr(TwilioSmsClient, "send", send)
    async_to_sync(sms_links.dispatch)(s.tenant, row.id)
    async_to_sync(sms_links.dispatch)(s.tenant, row.id)
    cfg = s.sms.model_dump(mode="json")
    cfg["enabled"] = False
    s.sms_path.write_text(json.dumps(cfg))
    async_to_sync(sms_links.dispatch)(s.tenant, row.id)
    assert send.await_count == 1
    with tenant_context(s.tenant):
        assert SmsNotification.objects.get(pk=row.id).state == "unknown"


@pytest.mark.parametrize("reason", ["revoked", "link", "expiry", "allowlist"])
def test_authority_rechecked_before_send(sms, monkeypatch, reason):
    s = sms
    row = invite(s)
    with tenant_context(s.tenant):
        if reason == "revoked":
            sms_links.revoke_sms(s.tenant, s.sid)
        elif reason == "link":
            CandidateLink.objects.filter(pk=row.link_id).update(revoked_at=timezone.now())
        elif reason == "expiry":
            SmsNotification.objects.filter(pk=row.id).update(expires_at=timezone.now())
        else:
            cfg = json.loads(s.path.read_text())
            cfg["participants"] = ["+34611111111"]
            s.path.write_text(json.dumps(cfg))
    send = AsyncMock()
    monkeypatch.setattr(TwilioSmsClient, "send", send)
    async_to_sync(sms_links.dispatch)(s.tenant, row.id)
    send.assert_not_awaited()
    with tenant_context(s.tenant):
        assert SmsNotification.objects.get(pk=row.id).state == "canceled"


@pytest.mark.parametrize(
    "state,answered,expected",
    [("completed", True, 2), ("no-answer", False, 1), ("busy", False, 1), ("completed", False, 1)],
)
def test_one_post_call_sms_same_conversation(sms, state, answered, expected):
    s = sms
    original = invite(s)
    call = reserve(s)
    with tenant_context(s.tenant):
        TelephoneAttempt.objects.filter(pk=call.id).update(
            attempted_at=timezone.now(),
            state="in-progress",
            stream_used_at=timezone.now() if answered else None,
        )
    for _ in range(3):
        r = callback(s, call, "status", CallStatus=state, CallDuration="30")
        assert r.status_code == 200, r.content
    with tenant_context(s.tenant):
        notices = list(SmsNotification.objects.order_by("created_at"))
        assert len(notices) == expected
        assert all(n.session_id == s.sid and n.link_id == original.link_id for n in notices)
        if expected == 2:
            assert notices[-1].purpose == "after_call"
            assert s.token in sms_links.sms_text(s.sms, notices[-1].link, "after_call")


def test_budget_concurrency_reservations_and_no_invented_delivery(sms):
    s = sms
    row = invite(s)
    with tenant_context(s.tenant):
        SmsNotification.objects.filter(pk=row.id).update(segment_count=30)
    with pytest.raises(NotAllowed, match="presupuesto"):
        sms_links.reserve_sms(
            s.tenant, s.sid, row.link_id, row.contact, "after_call:limit", "after_call"
        )
    assert sms_links.public_state(row)["state"] == "queued"


def test_signed_delivery_monotonic_and_forgery(sms):
    s = sms
    row = invite(s)
    async_to_sync(sms_links.claim)(s.tenant, row.id)
    url = s.sms.callback(row.id)
    path = url.removeprefix(s.sms.callback_base_url)
    params = dict(
        AccountSid=s.config.account_sid,
        MessageSid="SM" + "4" * 32,
        MessageStatus="delivered",
        To=row.contact,
        From=s.config.sender,
    )
    r = s.client.post(
        path,
        urlencode(params),
        content_type="application/x-www-form-urlencoded",
        HTTP_X_TWILIO_SIGNATURE="forged",
    )
    assert r.status_code == 403
    for state in ["delivered", "sent", "failed", "delivered"]:
        params["MessageStatus"] = state
        assert (
            s.client.post(
                path,
                urlencode(params),
                content_type="application/x-www-form-urlencoded",
                HTTP_X_TWILIO_SIGNATURE=sign(url, params),
            ).status_code
            == 204
        )
    with tenant_context(s.tenant):
        assert SmsNotification.objects.get(pk=row.id).state == "delivered"


def test_sms_transport_payload_and_ambiguity(sms):
    s = sms

    def handler(request):
        assert request.method == "POST"
        assert b"whatsapp" not in request.content
        assert b"StatusCallback" in request.content
        return httpx.Response(201, json={"sid": "SM" + "5" * 32})

    client = TwilioSmsClient(s.config, s.sms, httpx.MockTransport(handler))
    assert async_to_sync(client.send)("+34600000000", "Test link", uuid4()).startswith("SM")
    with pytest.raises(NotAllowed):
        async_to_sync(client.send)("+34900000000", "Test", uuid4())
    bad = TwilioSmsClient(s.config, s.sms, httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(SmsOutcomeUnknown):
        async_to_sync(bad.send)("+34600000000", "Test", uuid4())
    assert segments("a" * 160) == 1 and segments("a" * 161) == 2
    with pytest.raises(NotAllowed):
        segments("Hola 👋")


def test_sms_rows_are_isolated_by_tenant(sms):
    row = invite(sms)
    other = Tenant.objects.create(slug="other-sms", name="Other")
    with tenant_context(other.id):
        assert SmsNotification.objects.filter(pk=row.id).count() == 0
    with tenant_context(sms.tenant):
        assert SmsNotification.objects.filter(pk=row.id).count() == 1


@pytest.mark.parametrize("reason", ["consent", "link", "no_invitation"])
def test_postcall_notification_needs_valid_continuation_authority(sms, reason):
    s = sms
    notice = invite(s) if reason != "no_invitation" else None
    call = reserve(s)
    with tenant_context(s.tenant):
        TelephoneAttempt.objects.filter(pk=call.id).update(
            attempted_at=timezone.now(), state="in-progress", stream_used_at=timezone.now()
        )
        if reason == "consent":
            sms_links.revoke_sms(s.tenant, s.sid)
        elif reason == "link":
            CandidateLink.objects.filter(pk=notice.link_id).update(revoked_at=timezone.now())
    assert callback(s, call, "status", CallStatus="completed").status_code == 200
    with tenant_context(s.tenant):
        assert not SmsNotification.objects.filter(purpose="after_call").exists()
