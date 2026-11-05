import asyncio
import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.core.cache import cache
from django.db import connections
from django.utils import timezone

from adapters.channels.twilio_voice import CallOutcomeUnknown, TwilioVoiceConfig
from screening.domain.errors import NotAllowed
from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.conversations import eleven_direct, telephone, telephone_stream
from web.conversations.links import digest_token
from web.conversations.models import Application, CandidateLink, Session, TelephoneAttempt
from web.jobs.models import Job
from web.telephone_ingress import application
from web.tenancy.context import tenant_context

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def setup(phone, settings, tmp_path, monkeypatch):
    # External clock fixed in daytime; tomorrow keeps transport expiry valid too.
    clock = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0)
    monkeypatch.setattr(timezone, "now", lambda: clock)

    # Keep the transport clocks aligned with Django, including expiry assertions.
    class TransportClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = timezone.now()
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr("adapters.channels.twilio_voice.datetime", TransportClock)
    monkeypatch.setattr("adapters.channels.twilio_sms.datetime", TransportClock)
    client, tenant, app = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    opened = async_to_sync(service.open_session)(token)
    from screening.platform.voice_runtime import agent_registry_key

    session = async_to_sync(service.repo.get_session)(tenant, opened.session_id)
    registry = tmp_path / "agents.json"
    registry.write_text(json.dumps({agent_registry_key(session): {}}))
    settings.ELEVEN_DIRECT_AGENTS_FILE = str(registry)
    settings.JEV_MODE = "synthetic"
    config = TwilioVoiceConfig(
        tenant_id=tenant,
        account_sid="AC" + "1" * 32,
        auth_token="test-only-twilio-key",
        sender="+12025550100",
        public_base_url="https://calls.example.test",
        participants=("+34600000000",),
        enabled=True,
        max_attempts_per_mobile=2,
        expires_at=timezone.now() + timedelta(days=1),
    )
    path = tmp_path / "telephone.json"
    data = config.model_dump(mode="json")
    data["auth_token"] = config.auth_token.get_secret_value()
    path.write_text(json.dumps(data))
    settings.TWILIO_VOICE_CONFIG_FILE = str(path)
    return SimpleNamespace(
        client=client,
        tenant=tenant,
        app=app,
        token=token,
        sid=opened.session_id,
        config=config,
        path=path,
    )


def reserve(s, request_id=None, phone="+34600000000"):
    return telephone.reserve(s.tenant, s.sid, digest_token(s.token), request_id or uuid4(), phone)


def test_ordinary_candidate_can_request_an_unlisted_spanish_mobile(setup):
    s = setup
    with tenant_context(s.tenant):
        Application.objects.filter(pk=s.app).update(is_test=False)
    body = {
        "session_id": str(s.sid),
        "token": s.token,
        "request_id": str(uuid4()),
        "phone": "+34710000001",
        "consent": True,
    }
    response = s.client.post(
        "/api/v1/chat/telephone/request", body, content_type="application/json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["state"] == "queued"
    assert (
        s.client.post(
            "/api/v1/chat/telephone/request", body, content_type="application/json"
        ).json()
        == response.json()
    )
    with tenant_context(s.tenant):
        attempt = TelephoneAttempt.objects.get(pk=response.json()["id"])
        assert attempt.contact == "+34710000001" and attempt.consent_at
        assert attempt.reserved_usd_micros == 2_000_000
        assert Job.objects.filter(kind="telephone_dial", max_attempts=1).count() == 1


def test_ordinary_unlisted_call_can_be_claimed_and_answered(setup):
    s = setup
    with tenant_context(s.tenant):
        Application.objects.filter(pk=s.app).update(is_test=False)
    row = reserve(s, phone="+34600000001")
    assert async_to_sync(telephone.claim)(s.tenant, row.id) is not None
    response = callback(s, row, To="+34600000001")
    assert response.status_code == 200 and b"<Stream" in response.content


def test_voice_policy_allows_ordinary_phone_but_keeps_browser_scope(setup, settings, tmp_path):
    from screening.platform.channel_context import context_for
    from screening.platform.voice_runtime import agent_registry_key

    s = setup
    with tenant_context(s.tenant):
        Application.objects.filter(pk=s.app).update(is_test=False)
    session = async_to_sync(get_container().conversations.repo.get_session)(s.tenant, s.sid)
    registry = tmp_path / "voice-registry.json"
    registry.write_text(json.dumps({agent_registry_key(session): {}}))
    settings.ELEVEN_DIRECT_AGENTS_FILE = str(registry)
    assert async_to_sync(eleven_direct.enabled)(session, telephone=True)
    assert not async_to_sync(eleven_direct.enabled)(session)
    note = async_to_sync(context_for)(session, "web_text")
    assert '"telephone": true' in note and '"browser_voice": false' in note
    settings.JEV_MODE = "off"
    assert '"telephone": false' in async_to_sync(context_for)(session, "web_text")
    settings.JEV_MODE = "synthetic"
    for contact in ("+34600000001", "+34600000002"):
        row = reserve(s, phone=contact)
        with tenant_context(s.tenant):
            TelephoneAttempt.objects.filter(pk=row.id).update(state="completed")
    assert '"telephone": false' in async_to_sync(context_for)(session, "web_text")
    registry.write_text("{}")
    assert not async_to_sync(eleven_direct.enabled)(session, telephone=True)


def sign(url, params, key="test-only-twilio-key"):
    message = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(
        hmac.new(key.encode(), message.encode(), hashlib.sha1).digest()
    ).decode()


def callback(s, row, kind="answer", **overrides):
    params = {
        "AccountSid": s.config.account_sid,
        "CallSid": "CA" + "2" * 32,
        "From": s.config.sender,
        "To": "+34600000000",
        **overrides,
    }
    path = f"/api/v1/webhooks/twilio-voice/{s.tenant}/{row.id}/{kind}"
    return s.client.post(
        path,
        urlencode(params),
        content_type="application/x-www-form-urlencoded",
        HTTP_X_TWILIO_SIGNATURE=sign(s.config.callback(row.id, kind), params),
    )


def test_consent_link_and_spanish_mobile(setup):
    s = setup
    body = {
        "session_id": str(s.sid),
        "token": s.token,
        "request_id": str(uuid4()),
        "phone": "+34600000000",
        "consent": False,
    }
    assert (
        s.client.post(
            "/api/v1/chat/telephone/request", body, content_type="application/json"
        ).status_code
        == 400
    )
    body["consent"] = True
    body["phone"] = "+34900000000"
    assert (
        s.client.post(
            "/api/v1/chat/telephone/request", body, content_type="application/json"
        ).status_code
        == 400
    )
    body["phone"] = "+34600000000"
    response = s.client.post(
        "/api/v1/chat/telephone/request", body, content_type="application/json"
    )
    assert response.status_code == 200 and response.json()["state"] == "queued"
    assert (
        s.client.post(
            "/api/v1/chat/telephone/request", body, content_type="application/json"
        ).json()
        == response.json()
    )
    with tenant_context(s.tenant):
        assert TelephoneAttempt.objects.count() == 1
        assert Job.objects.filter(kind="telephone_dial", max_attempts=1).count() == 1
    body["token"] = "wrong"
    assert (
        s.client.post(
            "/api/v1/chat/telephone/request", body, content_type="application/json"
        ).status_code
        != 200
    )


def test_mobile_exhaustion_does_not_hide_capacity_for_other_candidates(setup):
    s = setup
    first = reserve(s)
    with tenant_context(s.tenant):
        TelephoneAttempt.objects.filter(pk=first.id).update(state="completed")
    second = reserve(s)
    # Terminal outcomes still consume their authorized attempt, not another call.
    with tenant_context(s.tenant):
        TelephoneAttempt.objects.filter(pk__in=[first.id, second.id]).update(state="completed")
    response = s.client.post(
        "/api/v1/chat/telephone/status",
        {"session_id": str(s.sid), "token": s.token},
        content_type="application/json",
    )
    assert response.status_code == 200 and response.json()["available"] is False
    assert telephone.call_capacity(s.tenant, s.config)
    with pytest.raises(NotAllowed, match="límite autorizado"):
        reserve(s)
    cfg = json.loads(s.path.read_text())
    cfg["max_attempts_per_mobile"] = 3
    s.path.write_text(json.dumps(cfg))
    assert telephone.call_capacity(s.tenant, telephone.configuration(s.tenant))


def test_empty_sms_allowlist_does_not_disable_telephone_capacity(setup):
    cfg = setup.config.model_copy(update={"participants": ()})
    assert telephone.call_capacity(setup.tenant, cfg)


@pytest.mark.parametrize("disabled", ["registry", "guardian"])
def test_local_voice_not_ready_never_consumes_a_call(setup, settings, disabled):
    if disabled == "registry":
        settings.ELEVEN_DIRECT_AGENTS_FILE = ""
    else:
        settings.JEV_MODE = "off"
    s = setup
    status = s.client.post(
        "/api/v1/chat/telephone/status",
        {
            "session_id": str(s.sid),
            "token": s.token,
        },
        content_type="application/json",
    )
    assert status.status_code == 200 and status.json()["available"] is False
    response = s.client.post(
        "/api/v1/chat/telephone/request",
        {
            "session_id": str(s.sid),
            "token": s.token,
            "request_id": str(uuid4()),
            "phone": "+34600000001",
            "consent": True,
        },
        content_type="application/json",
    )
    assert response.status_code == 400
    with tenant_context(s.tenant):
        assert not TelephoneAttempt.objects.exists()
        assert not Job.objects.filter(kind="telephone_dial").exists()


@pytest.mark.parametrize("hour", [2, 7, 22, 23])
def test_quiet_hours_reject_request_without_spending(setup, monkeypatch, hour):
    from zoneinfo import ZoneInfo

    now = timezone.now().astimezone(ZoneInfo("Europe/Madrid")).replace(hour=hour)
    monkeypatch.setattr(timezone, "now", lambda: now)
    with pytest.raises(NotAllowed, match="horario"):
        reserve(setup)
    assert not telephone.call_capacity(setup.tenant, setup.config, application_id=setup.app)
    with tenant_context(setup.tenant):
        assert not TelephoneAttempt.objects.exists()


def test_call_delayed_into_quiet_hours_is_canceled_not_rescheduled(setup, monkeypatch):
    from zoneinfo import ZoneInfo

    now = timezone.now().astimezone(ZoneInfo("Europe/Madrid")).replace(hour=21, minute=59)
    # Extend only this synthetic test's config to cover the boundary.
    cfg = json.loads(setup.path.read_text())
    cfg["expires_at"] = (now + timedelta(hours=2)).isoformat()
    setup.path.write_text(json.dumps(cfg))
    monkeypatch.setattr(timezone, "now", lambda: now)
    row = reserve(setup)
    later = now + timedelta(minutes=2)
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert async_to_sync(telephone.claim)(setup.tenant, row.id) is None
    with tenant_context(setup.tenant):
        assert TelephoneAttempt.objects.get(pk=row.id).state == "canceled"


def test_application_limit_cannot_be_bypassed_by_changing_mobile(setup):
    for contact in ("+34600000001", "+34710000001"):
        row = reserve(setup, phone=contact)
        with tenant_context(setup.tenant):
            TelephoneAttempt.objects.filter(pk=row.id).update(state="completed")
    with pytest.raises(NotAllowed, match="por candidatura"):
        reserve(setup, phone="+34600000002")


def test_concurrent_requests_reserve_only_one_attempt_and_budget(setup):
    barrier = Barrier(2)

    def request():
        try:
            barrier.wait(timeout=5)
            return reserve(setup, phone="+34600000001").id
        except NotAllowed:
            return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: request(), range(2)))
    assert sum(attempt is not None for attempt in attempts) == 1
    with tenant_context(setup.tenant):
        assert TelephoneAttempt.objects.count() == 1
        assert TelephoneAttempt.objects.get().reserved_usd_micros == 2_000_000
        assert Job.objects.filter(kind="telephone_dial").count() == 1


@pytest.mark.parametrize(
    "contact", ["+34900000001", "+34700000001", "+442071234567", "112", "+3460000000１"]
)
def test_invalid_destination_does_not_reserve_or_enqueue(setup, contact):
    s = setup
    response = s.client.post(
        "/api/v1/chat/telephone/request",
        {
            "session_id": str(s.sid),
            "token": s.token,
            "request_id": str(uuid4()),
            "phone": contact,
            "consent": True,
        },
        content_type="application/json",
    )
    assert response.status_code == 400
    with tenant_context(s.tenant):
        assert not TelephoneAttempt.objects.exists()
        assert not Job.objects.filter(kind="telephone_dial").exists()


@pytest.mark.parametrize("recording", [False, True, None])
def test_ordinary_unlisted_request_dispatches_once_with_verified_provider_policy(
    setup, settings, tmp_path, monkeypatch, recording
):
    from screening.platform.voice_runtime import (
        DIRECT_MODEL,
        agent_registry_key,
        policy_hash,
        tool_specs,
        voice_prompt,
    )

    s = setup
    with tenant_context(s.tenant):
        Application.objects.filter(pk=s.app).update(is_test=False)
    session = async_to_sync(get_container().conversations.repo.get_session)(s.tenant, s.sid)
    key, registry = tmp_path / "voice-key", tmp_path / "voice-registry.json"
    key.write_text("synthetic-test-only")
    registry.write_text(
        json.dumps(
            {
                agent_registry_key(session): {
                    "hash": policy_hash(session),
                    "agent_id": "synthetic",
                }
            }
        )
    )
    settings.ELEVENLABS_API_KEY_FILE = str(key)
    settings.ELEVEN_DIRECT_AGENTS_FILE = str(registry)
    settings.JEV_MODE = "synthetic"
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-only")
    destination = "+34710000001"
    outbound = []

    def provider(request):
        if request.url.host == "api.elevenlabs.io":
            assert request.method == "GET"
            if "/agents/" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "conversation_config": {
                            "realtime_model": DIRECT_MODEL,
                            "agent": {
                                "prompt": {
                                    "prompt": voice_prompt(session),
                                    "tools": tool_specs(session),
                                },
                            },
                        },
                        "platform_settings": {
                            "auth": {"enable_auth": True},
                            "privacy": {"record_voice": recording},
                        },
                    },
                )
            assert request.url.path == "/v1/convai/conversation/get-signed-url"
            return httpx.Response(200, json={"signed_url": "wss://api.elevenlabs.io/synthetic"})
        assert request.url.host == "api.twilio.com"
        if request.url.path.endswith("IncomingPhoneNumbers.json"):
            return httpx.Response(
                200,
                json={
                    "incoming_phone_numbers": [
                        {
                            "account_sid": s.config.account_sid,
                            "phone_number": s.config.sender,
                            "capabilities": {"voice": True},
                        }
                    ]
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200, json={"sid": s.config.account_sid, "type": "Full", "status": "active"}
            )
        assert request.url.path.endswith("/Calls.json")
        from urllib.parse import parse_qs

        form = parse_qs(request.content.decode())
        assert form["To"] == [destination] and form["TimeLimit"] == ["300"]
        assert form["Record"] == ["false"]
        outbound.append(form)
        return httpx.Response(
            201,
            json={
                "sid": "CA" + "2" * 32,
                "account_sid": s.config.account_sid,
                "to": destination,
                "from": s.config.sender,
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(
            **{
                **kw,
                "transport": httpx.MockTransport(provider),
            }
        ),
    )
    response = s.client.post(
        "/api/v1/chat/telephone/request",
        {
            "session_id": str(s.sid),
            "token": s.token,
            "request_id": str(uuid4()),
            "phone": destination,
            "consent": True,
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    with tenant_context(s.tenant):
        row = TelephoneAttempt.objects.get(pk=response.json()["id"])
    async_to_sync(telephone.dispatch)(s.tenant, row.id)
    async_to_sync(telephone.dispatch)(s.tenant, row.id)
    status = s.client.post(
        "/api/v1/chat/telephone/status",
        {
            "session_id": str(s.sid),
            "token": s.token,
        },
        content_type="application/json",
    )
    if recording is not False:
        assert status.json()["attempt"]["state"] == "failed"
        assert not outbound
        return
    assert status.json()["attempt"]["state"] == "ringing"
    assert len(outbound) == 1
    assert b"<Stream" in callback(s, row, To=destination).content

    async def connect_media():
        comm = ApplicationCommunicator(
            application,
            {
                "type": "websocket",
                "path": f"/api/v1/webhooks/twilio-voice/{s.tenant}/{row.id}/media",
                "headers": [
                    (b"x-twilio-signature", sign(s.config.callback(row.id, "media"), {}).encode())
                ],
                "query_string": b"",
            },
        )
        await comm.send_input({"type": "websocket.connect"})
        assert (await comm.receive_output())["type"] == "websocket.accept"
        await comm.send_input({"type": "websocket.disconnect", "code": 1000})
        await comm.wait()

    async_to_sync(connect_media)()


def test_authorized_ten_call_limit_keeps_history_and_global_budget(setup):
    s = setup
    cfg = json.loads(s.path.read_text())
    cfg["max_attempts_per_mobile"] = 10
    s.path.write_text(json.dumps(cfg))
    rows = []
    for _ in range(10):
        row = reserve(s)
        rows.append(row.id)
        with tenant_context(s.tenant):
            TelephoneAttempt.objects.filter(pk=row.id).update(state="completed")
    assert not telephone.call_capacity(s.tenant, telephone.configuration(s.tenant))
    with pytest.raises(NotAllowed, match="cupo autorizado"):
        reserve(s)
    with tenant_context(s.tenant):
        assert TelephoneAttempt.objects.filter(pk__in=rows).count() == 10


def test_request_status_does_not_disclose_private_destination(setup):
    row = reserve(setup)
    response = setup.client.post(
        "/api/v1/chat/telephone/status",
        {"token": setup.token, "session_id": str(setup.sid)},
        content_type="application/json",
    )
    assert response.json()["attempt"]["id"] == str(row.id)
    assert "+34600000000" not in response.content.decode()


def test_quotas_survive_terminal_calls_and_new_request_ids(setup):
    s = setup
    row = reserve(s)
    with pytest.raises(NotAllowed, match="pendiente"):
        reserve(s)
    with tenant_context(s.tenant):
        TelephoneAttempt.objects.filter(pk=row.id).update(state="completed")
    row2 = reserve(s)
    with tenant_context(s.tenant):
        TelephoneAttempt.objects.filter(pk=row2.id).update(state="failed")
    with pytest.raises(NotAllowed, match="dos intentos"):
        reserve(s)


def test_tenant_rls(setup):
    reserve(setup)
    with tenant_context(uuid4()):
        assert TelephoneAttempt.objects.count() == 0


@pytest.mark.parametrize("change", ["expired", "revoked", "human", "closed", "erased"])
def test_dispatch_revalidates_authorization(setup, change):
    s = setup
    row = reserve(s)
    with tenant_context(s.tenant):
        if change == "expired":
            TelephoneAttempt.objects.filter(pk=row.id).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
        elif change == "revoked":
            CandidateLink.objects.filter(application_id=s.app).update(revoked_at=timezone.now())
        elif change in {"human", "closed"}:
            Session.objects.filter(pk=s.sid).update(
                status="handed_off" if change == "human" else "completed"
            )
        else:
            from web.conversations.models import Application

            Application.objects.filter(pk=s.app).update(erased_at=timezone.now())
    assert async_to_sync(telephone.claim)(s.tenant, row.id) is None
    with tenant_context(s.tenant):
        assert TelephoneAttempt.objects.get(pk=row.id).state == "canceled"


def test_single_dispatch_after_timeout_and_repeated_job(setup, monkeypatch, settings):
    s = setup
    settings.JEV_MODE = "synthetic"
    row = reserve(s)
    monkeypatch.setattr(eleven_direct, "enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(eleven_direct, "signed_url", AsyncMock(return_value=("unused", {})))
    dial = AsyncMock(side_effect=CallOutcomeUnknown("uncertain"))
    monkeypatch.setattr(
        telephone, "TwilioVoiceClient", lambda _: SimpleNamespace(preflight=AsyncMock(), dial=dial)
    )
    async_to_sync(telephone.dispatch)(s.tenant, row.id)
    monkeypatch.setattr(eleven_direct, "enabled", AsyncMock(side_effect=RuntimeError("offline")))
    async_to_sync(telephone.dispatch)(s.tenant, row.id)
    assert dial.await_count == 1
    with tenant_context(s.tenant):
        actual = TelephoneAttempt.objects.get(pk=row.id)
        assert actual.state == "unknown" and actual.reserved_usd_micros == 2_000_000


def test_preflight_failure_is_visible_without_dialing(setup, monkeypatch):
    row = reserve(setup)
    monkeypatch.setattr(eleven_direct, "enabled", AsyncMock(return_value=False))
    async_to_sync(telephone.dispatch)(setup.tenant, row.id)
    with tenant_context(setup.tenant):
        actual = TelephoneAttempt.objects.get(pk=row.id)
        assert actual.state == "failed" and actual.attempted_at is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("AccountSid", "AC" + "9" * 32),
        ("CallSid", "invalid"),
        ("To", "+34611111111"),
        ("From", "+12025550199"),
    ],
)
def test_even_signed_callback_must_match_bound_call(setup, field, value):
    row = reserve(setup)
    async_to_sync(telephone.claim)(setup.tenant, row.id)
    assert callback(setup, row, **{field: value}).status_code == 403


def test_answer_and_out_of_order_status_callback(setup):
    s = setup
    row = reserve(s)
    assert callback(s, row).status_code == 403
    async_to_sync(telephone.claim)(s.tenant, row.id)
    answer = callback(s, row)
    assert (
        answer.status_code == 200
        and b"<Stream" in answer.content
        and b"<Hangup/>" in answer.content
    )
    callback(s, row, "status", CallStatus="completed", CallDuration="30")
    callback(s, row, "status", CallStatus="ringing")
    async_to_sync(telephone.result)(s.tenant, row.id, "ringing", "CA" + "2" * 32)
    with tenant_context(s.tenant):
        actual = TelephoneAttempt.objects.get(pk=row.id)
        assert actual.state == "completed" and actual.duration_seconds == 30
    assert b"Stream" not in callback(s, row).content
    assert cache.get("live-call:" + str(s.sid)) is None


def test_unsigned_callback_and_replay_to_other_attempt_fail(setup):
    s = setup
    row = reserve(s)
    path = f"/api/v1/webhooks/twilio-voice/{s.tenant}/{row.id}/answer"
    assert (
        s.client.post(path, {}, content_type="application/x-www-form-urlencoded").status_code == 403
    )
    async_to_sync(telephone.claim)(s.tenant, row.id)
    assert callback(s, row).status_code == 200
    callback(s, row, "status", CallStatus="completed")
    second = reserve(s)
    async_to_sync(telephone.claim)(s.tenant, second.id)
    # Even a correctly signed answer cannot rebind a CallSid owned by another attempt.
    assert callback(s, second).status_code == 403


async def test_ingress_never_exposes_dashboard_or_candidate_request(setup):
    for path in [
        "/",
        "/api/v1/health",
        "/api/v1/chat/telephone/request",
        "/ws/chat/",
        "/api/v1/webhooks/twilio-voice/bad",
    ]:
        comm = ApplicationCommunicator(
            application,
            {"type": "http", "method": "POST", "path": path, "headers": [], "query_string": b""},
        )
        await comm.send_input({"type": "http.request", "body": b""})
        assert (await comm.receive_output())["status"] == 404
        await comm.wait()


async def test_stream_handshake_requires_signature(setup):
    s = setup
    comm = ApplicationCommunicator(
        application,
        {
            "type": "websocket",
            "path": f"/api/v1/webhooks/twilio-voice/{s.tenant}/{uuid4()}/media",
            "headers": [],
            "query_string": b"",
        },
    )
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output())["code"] == 4403
    await comm.send_input({"type": "websocket.disconnect", "code": 4403})
    await comm.wait()


@pytest.mark.parametrize("revoke", ["human", "disable", "origin", "link"])
async def test_media_codec_and_immediate_revocation(setup, monkeypatch, revoke):
    s = setup
    row = await sync_to_async(reserve)(s, phone="+34600000001")
    await telephone.claim(s.tenant, row.id)
    await sync_to_async(callback)(s, row, To="+34600000001")
    monkeypatch.setattr(telephone_stream, "enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(telephone_stream, "VoiceRuntime", lambda _, **kwargs: SimpleNamespace())
    stop = AsyncMock(return_value=True)
    monkeypatch.setattr(telephone_stream, "TwilioVoiceClient", lambda _: SimpleNamespace(stop=stop))
    received = []

    async def fake_run(consumer):
        consumer.bridge = SimpleNamespace(
            finished=asyncio.Event(),
            audio=AsyncMock(side_effect=lambda pcm: received.append(pcm)),
            stop=lambda: consumer.bridge.finished.set(),
        )
        await consumer.emit({"type": "audio", "delta": base64.b64encode(b"\0" * 320).decode()})
        await consumer.watch()
        await consumer.release()
        await consumer.close()

    monkeypatch.setattr(telephone_stream.TelephoneConsumer, "run_voice", fake_run)
    url = s.config.callback(row.id, "media")
    comm = ApplicationCommunicator(
        application,
        {
            "type": "websocket",
            "path": f"/api/v1/webhooks/twilio-voice/{s.tenant}/{row.id}/media",
            "headers": [(b"x-twilio-signature", sign(url, {}).encode())],
            "query_string": b"",
        },
    )
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output())["type"] == "websocket.accept"
    stream = "MZ" + "3" * 32
    start = {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": stream,
        "start": {
            "accountSid": s.config.account_sid,
            "callSid": "CA" + "2" * 32,
            "streamSid": stream,
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
    }
    await comm.send_input({"type": "websocket.receive", "text": json.dumps(start)})
    media = json.loads((await comm.receive_output())["text"])
    assert media["event"] == "media" and base64.b64decode(media["media"]["payload"])
    assert json.loads((await comm.receive_output())["text"])["event"] == "mark"
    await comm.send_input(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "event": "media",
                    "sequenceNumber": "2",
                    "streamSid": stream,
                    "media": {
                        "track": "inbound",
                        "payload": base64.b64encode(b"\xff" * 160).decode(),
                    },
                }
            ),
        }
    )
    await asyncio.sleep(0.05)
    assert received

    @sync_to_async
    def human():
        from web.accounts.models import User

        with tenant_context(s.tenant):
            Session.objects.filter(pk=s.sid).update(
                human_owner=User.objects.get(email="reclutadora@orbio.local"), status="handed_off"
            )

    if revoke == "human":
        await human()
    elif revoke == "link":

        @sync_to_async
        def revoke_link():
            with tenant_context(s.tenant):
                CandidateLink.objects.filter(application_id=s.app).update(revoked_at=timezone.now())

        await revoke_link()
    else:
        data = json.loads(s.path.read_text())
        if revoke == "disable":
            data["enabled"] = False
        else:
            data["public_base_url"] = "https://other.example.test"
        s.path.write_text(json.dumps(data))
    assert json.loads((await comm.receive_output(timeout=2))["text"])["event"] == "clear"
    assert (await comm.receive_output())["type"] == "websocket.close"
    assert stop.await_count == 1
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


def test_global_budget_is_not_reset_by_terminal_states(setup):
    row = reserve(setup)
    with tenant_context(setup.tenant):
        TelephoneAttempt.objects.filter(pk=row.id).update(
            state="completed", reserved_usd_micros=20_000_000
        )
    with pytest.raises(NotAllowed, match="cupo autorizado"):
        reserve(setup)


def test_redaction_keeps_budget_but_removes_contact(setup):
    from web.conversations.privacy import redact_candidate

    row = reserve(setup)
    redact_candidate(setup.tenant, setup.app, "synthetic-request", apply=True)
    with tenant_context(setup.tenant):
        actual = TelephoneAttempt.objects.get(pk=row.id)
        assert actual.state == "canceled"
        assert actual.contact == actual.contact_digest == actual.link_digest == ""
        assert actual.reserved_usd_micros == 2_000_000
        assert Job.objects.get(kind="telephone_dial").status == "cancelled"


def test_redaction_refuses_unreconciled_call(setup):
    from screening.domain.errors import Conflict
    from web.conversations.privacy import redact_candidate

    row = reserve(setup)
    async_to_sync(telephone.claim)(setup.tenant, row.id)
    with pytest.raises(Conflict, match="llamada telefónica"):
        redact_candidate(setup.tenant, setup.app, "synthetic-request", apply=True)
