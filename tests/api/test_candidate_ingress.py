import json
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.utils import timezone

from tests.api.test_sms_links import phone, setup, sms
from web.candidate_ingress import application
from web.conversations.links import digest_token
from web.conversations.models import Application, CandidateLink, SmsNotification, TelephoneAttempt
from web.core.security import issue_socket_ticket
from web.tenancy.context import tenant_context

__all__ = ["phone", "setup", "sms"]
pytestmark = pytest.mark.django_db(transaction=True)


async def request(
    s,
    path,
    method="GET",
    data=None,
    origin="https://candidate.example.test",
    host="candidate.example.test",
):
    body = json.dumps(data).encode() if data is not None else b""
    comm = ApplicationCommunicator(
        application,
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [
                (b"host", host.encode()),
                (b"origin", origin.encode()),
                (b"content-type", b"application/json"),
            ],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 1234),
            "http_version": "1.1",
        },
    )
    await comm.send_input({"type": "http.request", "body": body, "more_body": False})
    response = await comm.receive_output(timeout=5)
    chunks = []
    while True:
        event = await comm.receive_output(timeout=5)
        chunks.append(event.get("body", b""))
        if not event.get("more_body", False):
            break
    await comm.send_input({"type": "http.disconnect"})
    await comm.wait()
    return response["status"], b"".join(chunks), dict(response["headers"])


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/api/v1/health",
        "/api/v1/docs",
        "/api/v1/tenants",
        "/api/v1/auth/login",
        "/admin/",
        "/src/main.tsx",
        "/.env",
        "/@vite/client",
        "/assets/../index.html",
        "/apply/grupo_sazon/cocina-ruzafa",
    ],
)
def test_private_surface_stays_closed(sms, path):
    assert async_to_sync(request)(sms, path)[0] == 404
    assert async_to_sync(request)(sms, path, "POST", {})[0] == 404


def test_static_link_scope_and_headers(sms, settings, tmp_path):
    settings.CANDIDATE_DIST_DIR = str(tmp_path)
    (tmp_path / "index.html").write_text("<h1>Candidate</h1>")
    status, body, headers = async_to_sync(request)(sms, "/c/" + sms.token)
    assert status == 200 and b"Candidate" in body
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert headers[b"cache-control"] == b"no-store"
    assert async_to_sync(request)(sms, "/c/" + "a" * 64)[0] == 404
    assert async_to_sync(request)(sms, "/c/" + sms.token, host="attacker.test")[0] == 404
    CandidateLink.objects.filter(application_id=sms.app).update(revoked_at=timezone.now())
    assert async_to_sync(request)(sms, "/c/" + sms.token)[0] == 404


def test_candidate_api_auth_and_expiry(sms):
    s = sms
    status, body, _ = async_to_sync(request)(s, "/api/v1/chat/sessions", "POST", {"token": s.token})
    assert status == 200, body
    assert json.loads(body)["session_id"] == str(s.sid)
    assert (
        async_to_sync(request)(
            s, "/api/v1/chat/sessions", "POST", {"token": s.token}, origin="https://evil.test"
        )[0]
        == 404
    )
    assert (
        async_to_sync(request)(
            s,
            "/api/v1/chat/telephone/status",
            "POST",
            {"token": s.token, "session_id": str(uuid4())},
        )[0]
        == 404
    )
    cfg = s.sms.model_dump(mode="json")
    cfg["expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
    s.sms_path.write_text(json.dumps(cfg))
    assert async_to_sync(request)(s, "/api/v1/chat/sessions", "POST", {"token": s.token})[0] == 410


def test_ordinary_candidate_can_use_personal_link_to_request_call_not_sms(sms, settings, tmp_path):
    s = sms
    with tenant_context(s.tenant):
        assert Application.objects.filter(pk=s.app).update(is_test=False) == 1
    settings.CANDIDATE_DIST_DIR = str(tmp_path)
    (tmp_path / "index.html").write_text("<h1>Candidate</h1>")
    assert async_to_sync(request)(s, "/c/" + s.token)[0] == 200
    assert async_to_sync(request)(s, "/api/v1/chat/sessions", "POST", {"token": s.token})[0] == 200
    body = dict(
        token=s.token,
        session_id=str(s.sid),
        request_id=str(uuid4()),
        phone="+34710000001",
        consent=False,
    )
    assert async_to_sync(request)(s, "/api/v1/chat/telephone/request", "POST", body)[0] == 400
    body["consent"] = True
    status, content, _ = async_to_sync(request)(s, "/api/v1/chat/telephone/request", "POST", body)
    assert status == 200, content
    assert json.loads(content)["state"] == "queued"
    with tenant_context(s.tenant):
        assert TelephoneAttempt.objects.get(session_id=s.sid).contact == "+34710000001"
    assert async_to_sync(request)(s, "/api/v1/chat/sms-link", "POST", body)[0] == 400
    for suffix in ("/voice", "/voice/config"):
        assert (
            async_to_sync(request)(s, f"/api/v1/chat/sessions/{s.sid}{suffix}", "POST", body)[0]
            == 404
        )
    with tenant_context(s.tenant):
        assert not SmsNotification.objects.exists()
        CandidateLink.objects.filter(application_id=s.app).update(revoked_at=timezone.now())
    assert async_to_sync(request)(s, "/c/" + s.token)[0] == 404


async def test_public_socket_needs_ticket_and_origin(sms):
    s = sms
    ticket = await sync_to_async(issue_socket_ticket)(s.tenant, s.sid, digest_token(s.token))
    for path, origin, expected in [
        (f"/ws/chat/{s.sid}/", "https://evil.test", "websocket.close"),
        ("/ws/health/", "https://candidate.example.test", "websocket.close"),
        (f"/ws/chat/{s.sid}/", "https://candidate.example.test", "websocket.accept"),
    ]:
        comm = ApplicationCommunicator(
            application,
            {
                "type": "websocket",
                "path": path,
                "query_string": ("ticket=" + ticket).encode(),
                "headers": [(b"host", b"candidate.example.test"), (b"origin", origin.encode())],
            },
        )
        await comm.send_input({"type": "websocket.connect"})
        assert (await comm.receive_output(timeout=5))["type"] == expected
        await comm.send_input({"type": "websocket.disconnect", "code": 1000})
        await comm.wait(timeout=5)
