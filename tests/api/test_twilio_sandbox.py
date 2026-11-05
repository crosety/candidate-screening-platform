import base64
import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
import pytest
from asgiref.sync import async_to_sync
from django.db import connection
from django.test import Client
from django.utils import timezone

from adapters.channels.twilio import TwilioClient, TwilioConfig
from tests.api.test_public_application import workspace
from tests.contracts.test_twilio_adapter import config
from web.conversations.models import (
    Application,
    ChannelDelivery,
    ChannelIdentity,
    ChannelInbound,
    Event,
    Message,
    Session,
    TwilioOutbound,
)
from web.conversations.twilio_runtime import claim_send, dispatch, queue_text, receive, record_send
from web.jobs.models import Job
from web.tenancy.context import tenant_context

__all__ = ["workspace"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def sandbox(
    workspace: tuple[Client, UUID], settings: Any, tmp_path: Path
) -> tuple[Client, TwilioConfig]:
    client, tenant = workspace
    cfg = config(tenant_id=str(tenant))
    path = tmp_path / "twilio.json"
    # Only synthetic credentials in tests; production files remain outside Git.
    path.write_text(
        json.dumps({**cfg.model_dump(mode="json"), "auth_token": cfg.auth_token.get_secret_value()})
    )
    settings.TWILIO_CONFIG_FILE = str(path)
    return client, cfg


def signed(
    client: Client, cfg: TwilioConfig, data: dict[str, str], kind: str = "inbound", **headers: str
) -> Any:
    url = cfg.callback(kind)
    raw = url + "".join(k + data[k] for k in sorted(data))
    signature = base64.b64encode(
        hmac.new(cfg.auth_token.get_secret_value().encode(), raw.encode(), hashlib.sha1).digest()
    ).decode()
    return client.post(
        url.split("test.example", 1)[1],
        urlencode(data),
        content_type="application/x-www-form-urlencoded",
        HTTP_X_TWILIO_SIGNATURE=signature,
        **headers,
    )


def incoming(
    client: Client, cfg: TwilioConfig, text: str, sid: str = "", run: bool = True, **overrides: str
) -> UUID:
    sid = sid or "SM" + uuid4().hex
    data = {
        "AccountSid": cfg.account_sid,
        "MessageSid": sid,
        "From": "whatsapp:" + cfg.participants[0],
        "To": "whatsapp:" + cfg.sender,
        "Body": text,
        "NumMedia": "0",
        **overrides,
    }
    response = signed(client, cfg, data)
    assert response.status_code == 200, response.content
    with tenant_context(cfg.tenant_id):
        row = ChannelInbound.objects.get(provider_id="twilio:" + sid)
    if run:
        async_to_sync(receive)(cfg.tenant_id, row.id)
    return row.id


def start(client: Client, cfg: TwilioConfig, accept: str = "ACEPTO 1") -> UUID:
    incoming(client, cfg, "Hola")
    incoming(client, cfg, accept)
    incoming(client, cfg, "Laura Prueba")
    with tenant_context(cfg.tenant_id):
        identity = ChannelIdentity.objects.get(provider="twilio")
        return Session.objects.get(application_id=identity.application_id).id


def test_webhook_security_durable_dedupe_and_redacted_job(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    client, cfg = sandbox
    url = f"/api/v1/webhooks/twilio/{cfg.tenant_id}/inbound"
    assert client.get(url).status_code == 405
    assert client.post(url, "bad", content_type="application/json").status_code == 400
    assert (
        client.post(url, "Body=x", content_type="application/x-www-form-urlencoded").status_code
        == 403
    )
    data = {
        "AccountSid": cfg.account_sid,
        "MessageSid": "SM" + "a" * 32,
        "From": "whatsapp:" + cfg.participants[0],
        "To": "whatsapp:" + cfg.sender,
        "Body": "Hola",
    }
    assert signed(client, cfg, data, HTTP_X_FORWARDED_HOST="attacker.example").status_code == 200
    assert signed(client, cfg, data).status_code == 200
    jobs = Job.objects.filter(kind="twilio_inbound")
    assert jobs.count() == 1 and set(jobs.get().payload) == {"inbound_id"}
    assert jobs.get().max_attempts == 1
    for key, value in [
        ("AccountSid", "AC" + "f" * 32),
        ("From", "whatsapp:+34600000009"),
        ("To", "whatsapp:+34600000008"),
    ]:
        assert signed(client, cfg, data | {key: value}).status_code == 403
    assert jobs.count() == 1


@pytest.mark.parametrize(("accept", "language"), [("ACEPTO 1", "es"), ("accept 1", "en")])
def test_menu_consent_each_offer_greeting_and_dashboard(
    sandbox: tuple[Client, TwilioConfig], accept: str, language: str
) -> None:
    client, cfg = sandbox
    with tenant_context(cfg.tenant_id):
        before = Application.objects.count()
    sid = start(client, cfg, accept)
    with tenant_context(cfg.tenant_id):
        assert Application.objects.count() == before + 1
        session = Session.objects.select_related("application").get(pk=sid)
        assert session.channel == "whatsapp" and session.application.phone == cfg.participants[0]
        assert session.application.is_test and not session.application.is_simulation
        identity = ChannelIdentity.objects.get(provider="twilio")
        assert session.application.job_ref == identity.onboarding["offers"][0] == "repartidores"
        assert session.language == language
        menu = TwilioOutbound.objects.filter(message__isnull=True).earliest("created_at").text
        assert "ACCEPT 1" in menu and "ACEPTO 1" in menu
        assert not any(word in menu.casefold() for word in ("prueba", "ficti", "demo", "orbio"))
        assert Event.objects.filter(session=session, type="whatsapp_consent").exists()
        assert Message.objects.filter(session=session).first().direction == "out"
        assert TwilioOutbound.objects.filter(message__session=session).count() == 1
    assert client.get(f"/api/v1/tenants/{cfg.tenant_id}/workspace/{sid}").status_code == 200


def test_no_implicit_consent_duplicate_work_and_unsupported_media(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    client, cfg = sandbox
    key = incoming(client, cfg, "Laura Prueba")
    incoming(client, cfg, "1")
    incoming(client, cfg, "photo", NumMedia="1")
    with tenant_context(cfg.tenant_id):
        assert ChannelIdentity.objects.get(provider="twilio").application_id is None
        before = TwilioOutbound.objects.count()
    async_to_sync(receive)(cfg.tenant_id, key)
    with tenant_context(cfg.tenant_id):
        assert TwilioOutbound.objects.count() == before


@pytest.mark.parametrize("refusal", ["No quiero", "no gracias", "Prefiero no dar mi nombre"])
def test_name_refusal_does_not_create_application_or_start_interview(
    sandbox: tuple[Client, TwilioConfig], refusal: str
) -> None:
    client, cfg = sandbox
    incoming(client, cfg, "Hola")
    incoming(client, cfg, "ACEPTO 1")
    with tenant_context(cfg.tenant_id):
        applications_before = Application.objects.count()
        messages_before = Message.objects.count()
    incoming(client, cfg, refusal)
    with tenant_context(cfg.tenant_id):
        identity = ChannelIdentity.objects.get(provider="twilio")
        assert identity.application_id is None and not identity.opted_out
        assert Application.objects.count() == applications_before
        assert Message.objects.count() == messages_before
        assert TwilioOutbound.objects.latest("created_at").text.startswith(
            "De acuerdo, no iniciaré"
        )
    incoming(client, cfg, "María del Mar Prueba")
    with tenant_context(cfg.tenant_id):
        identity.refresh_from_db()
        assert identity.application_id is not None
        assert identity.application.candidate_name == "María del Mar Prueba"
        assert Application.objects.count() == applications_before + 1


def test_human_takeover_no_echo_delivers_via_whatsapp_and_resumes(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    client, cfg = sandbox
    sid = start(client, cfg)
    incoming(client, cfg, "persona")
    url = f"/api/v1/tenants/{cfg.tenant_id}/sessions/{sid}/human"
    assert client.post(url, {"action": "claim"}, content_type="application/json").status_code == 200
    with tenant_context(cfg.tenant_id):
        before = TwilioOutbound.objects.count()
    incoming(client, cfg, "¿A qué hora es el turno?")
    with tenant_context(cfg.tenant_id):
        assert TwilioOutbound.objects.count() == before  # Do not echo incoming text.
        ai_rows = list(
            TwilioOutbound.objects.filter(message__session_id=sid).values_list("id", flat=True)
        )
    for row in ai_rows:
        assert async_to_sync(claim_send)(cfg.tenant_id, row, cfg) is None
    payload = {
        "action": "message",
        "text": "Hola, soy Marta de selección.",
        "message_id": str(uuid4()),
    }
    for _ in range(2):
        assert client.post(url, payload, content_type="application/json").status_code == 200
    with tenant_context(cfg.tenant_id):
        row = TwilioOutbound.objects.get(message__session_id=sid, message__channel="human")
        assert row.text == payload["text"] and row.state == "pending"
        assert all(
            r == "human_takeover"
            for r in TwilioOutbound.objects.filter(id__in=ai_rows).values_list("reason", flat=True)
        )
    assert async_to_sync(claim_send)(cfg.tenant_id, row.id, cfg) == (
        cfg.participants[0],
        payload["text"],
    )
    assert (
        client.post(url, {"action": "resume_ai"}, content_type="application/json").json()["status"]
        == "active"
    )


def test_optout_blocks_queued_and_new_conversation(sandbox: tuple[Client, TwilioConfig]) -> None:
    client, cfg = sandbox
    sid = start(client, cfg)
    incoming(client, cfg, "BAJA")
    incoming(client, cfg, "NUEVA")
    with tenant_context(cfg.tenant_id):
        identity = ChannelIdentity.objects.get(provider="twilio")
        assert identity.opted_out and Session.objects.get(pk=sid).status == "abandoned"
        rows = list(TwilioOutbound.objects.values_list("id", flat=True))
    for row in rows:
        assert async_to_sync(claim_send)(cfg.tenant_id, row, cfg) is None


def test_closed_conversation_keeps_late_message_new_keeps_history(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    client, cfg = sandbox
    old = start(client, cfg)
    with tenant_context(cfg.tenant_id):
        Session.objects.filter(pk=old).update(status="completed")
    incoming(client, cfg, "Una duda más")
    incoming(client, cfg, "NUEVA")
    incoming(client, cfg, "ACEPTO 1")
    incoming(client, cfg, "Segunda Prueba")
    with tenant_context(cfg.tenant_id):
        assert Event.objects.filter(session_id=old, type="late_inbound_needs_review").exists()
        identity = ChannelIdentity.objects.get(provider="twilio")
        assert Session.objects.get(application_id=identity.application_id).id != old
        assert Message.objects.filter(session_id=old).exists()


def test_window_budget_cadence_and_unknown_no_retry(
    sandbox: tuple[Client, TwilioConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, cfg = sandbox
    incoming(client, cfg, "Hola")
    with tenant_context(cfg.tenant_id):
        row = TwilioOutbound.objects.get()
    calls = []

    async def timeout(self: TwilioClient, contact: str, text: str) -> str:
        calls.append(text)
        raise httpx.ReadTimeout("secret provider details")

    monkeypatch.setattr(TwilioClient, "send", timeout)
    with pytest.raises(ValueError, match="sin reenvío"):
        async_to_sync(dispatch)(cfg.tenant_id, row.id)
    async_to_sync(dispatch)(cfg.tenant_id, row.id)
    assert len(calls) == 1
    with tenant_context(cfg.tenant_id):
        row.refresh_from_db()
        assert row.state == "unknown" and "secret" not in row.reason
    incoming(client, cfg, "hola")
    with tenant_context(cfg.tenant_id):
        next_row = TwilioOutbound.objects.exclude(pk=row.id).get()
    assert async_to_sync(claim_send)(cfg.tenant_id, next_row.id, cfg) is None
    assert Job.objects.filter(idempotency_key__startswith="twilio:defer:").exists()
    with tenant_context(cfg.tenant_id):
        TwilioOutbound.objects.filter(pk=row.id).update(
            attempted_at=timezone.now() - timedelta(seconds=5)
        )
    assert (
        async_to_sync(claim_send)(
            cfg.tenant_id, next_row.id, cfg.model_copy(update={"max_outbound_per_day": 1})
        )
        is None
    )
    incoming(client, cfg, "hola")
    with tenant_context(cfg.tenant_id):
        expired = TwilioOutbound.objects.order_by("-created_at").first()
        ChannelIdentity.objects.update(last_inbound_at=timezone.now() - timedelta(hours=25))
    assert async_to_sync(claim_send)(cfg.tenant_id, expired.id, cfg) is None
    with tenant_context(cfg.tenant_id):
        expired.refresh_from_db()
        assert expired.reason == "window_expired"


def test_delivery_callback_before_post_returns_and_out_of_order(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    client, cfg = sandbox
    incoming(client, cfg, "Hola")
    sid = "SM" + "d" * 32
    data = {"AccountSid": cfg.account_sid, "MessageSid": sid, "MessageStatus": "read"}
    assert signed(client, cfg, data, "status").status_code == 200
    with tenant_context(cfg.tenant_id):
        row = TwilioOutbound.objects.get()
    async_to_sync(record_send)(cfg.tenant_id, row.id, sid)
    for status in ["sent", "delivered", "failed", "queued"]:
        assert signed(client, cfg, data | {"MessageStatus": status}, "status").status_code == 200
    with tenant_context(cfg.tenant_id):
        row.refresh_from_db()
        assert row.state == ChannelDelivery.objects.get(provider_id=sid).status == "read"


def test_hr_fifo_dashboard_delivery_and_no_double_send(
    sandbox: tuple[Client, TwilioConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, cfg = sandbox
    sid = start(client, cfg)
    url = f"/api/v1/tenants/{cfg.tenant_id}/sessions/{sid}/human"
    assert client.post(url, {"action": "claim"}, content_type="application/json").status_code == 200
    for text in ["Primera respuesta de HR", "Segunda respuesta de HR"]:
        response = client.post(
            url,
            {"action": "message", "text": text, "message_id": str(uuid4())},
            content_type="application/json",
        )
        assert response.status_code == 200
    with tenant_context(cfg.tenant_id):
        ai = TwilioOutbound.objects.get(message__session_id=sid, message__channel="whatsapp")
        rows = list(TwilioOutbound.objects.filter(message__channel="human").order_by("created_at"))
    assert async_to_sync(claim_send)(cfg.tenant_id, ai.id, cfg) is None
    # A worker processing a later message first cannot overtake the previous one.
    assert async_to_sync(claim_send)(cfg.tenant_id, rows[1].id, cfg) is None
    calls = []
    provider_id = "SM" + "b" * 32

    async def accepted(self: TwilioClient, contact: str, text: str) -> str:
        calls.append(text)
        return provider_id

    monkeypatch.setattr(TwilioClient, "send", accepted)
    async_to_sync(dispatch)(cfg.tenant_id, rows[0].id)
    async_to_sync(dispatch)(cfg.tenant_id, rows[0].id)
    assert calls == [rows[0].text]
    detail_url = f"/api/v1/tenants/{cfg.tenant_id}/workspace/{sid}"
    human = [m for m in client.get(detail_url).json()["messages"] if m["channel"] == "human"]
    assert human[0]["delivery_status"] == "pending"  # HTTP 201 is not mobile delivery.
    assert (
        signed(
            client,
            cfg,
            {
                "AccountSid": cfg.account_sid,
                "MessageSid": provider_id,
                "MessageStatus": "delivered",
            },
            "status",
        ).status_code
        == 200
    )
    human = [m for m in client.get(detail_url).json()["messages"] if m["channel"] == "human"]
    assert human[0]["delivery_status"] == "delivered"
    with tenant_context(cfg.tenant_id):
        TwilioOutbound.objects.filter(pk=rows[0].id).update(
            attempted_at=timezone.now() - timedelta(seconds=4)
        )
    assert async_to_sync(claim_send)(cfg.tenant_id, rows[1].id, cfg) == (
        cfg.participants[0],
        rows[1].text,
    )


def test_intake_supersedes_old_menu_length_and_rls(sandbox: tuple[Client, TwilioConfig]) -> None:
    client, cfg = sandbox
    incoming(client, cfg, "hola")
    incoming(client, cfg, "ACEPTO 1")
    with tenant_context(cfg.tenant_id):
        assert TwilioOutbound.objects.filter(state="pending").count() == 1
        assert TwilioOutbound.objects.filter(reason="intake_updated").count() == 1
        identity = ChannelIdentity.objects.get(provider="twilio")
        queue_text(identity, "x" * 1601, "invalid-length")
        row = TwilioOutbound.objects.get(key="invalid-length")
    assert async_to_sync(claim_send)(cfg.tenant_id, row.id, cfg) is None
    with tenant_context(cfg.tenant_id):
        row.refresh_from_db()
        assert row.reason == "invalid_message_length" and row.attempted_at is None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname='conversations_twiliooutbound'"
        )
        assert cursor.fetchone() == (True, True)
    with tenant_context(uuid4()):
        assert not TwilioOutbound.objects.exists()


def test_changed_offer_requires_new_selection(sandbox: tuple[Client, TwilioConfig]) -> None:
    from adapters.persistence.profiles import DjangoProfileRepository
    from screening.domain.profile import ProfileContent
    from web.accounts.models import User
    from web.profiles.models import Publication

    client, cfg = sandbox
    incoming(client, cfg, "Hola")
    with tenant_context(cfg.tenant_id):
        publication = Publication.objects.select_related("version").get(state="active", segment="")
        profile = ProfileContent.model_validate(publication.version.content)
        profile.offers[0].status = "closed"
        repo = DjangoProfileRepository()
        actor = User.objects.get(email="fde@orbio.local").id
        updated = repo.create_version(cfg.tenant_id, profile, "medium", "Cierre", actor)
        next_pub = repo.create_publication(
            cfg.tenant_id, updated.id, publication.version_id, 100, False, False, "Cierre", actor
        )
        repo.activate(cfg.tenant_id, next_pub.id)
    incoming(client, cfg, "ACEPTO 1")
    with tenant_context(cfg.tenant_id):
        identity = ChannelIdentity.objects.get(provider="twilio")
        assert identity.application_id is None and "selected" not in identity.onboarding
        assert "cocina-benimaclet" not in identity.onboarding["offers"]
        assert "han cambiado" in TwilioOutbound.objects.get(state="pending").text


def test_meta_identity_cannot_be_silently_rebound(sandbox: tuple[Client, TwilioConfig]) -> None:
    client, cfg = sandbox
    with tenant_context(cfg.tenant_id):
        ChannelIdentity.objects.create(
            tenant_id=cfg.tenant_id,
            channel="whatsapp",
            contact=cfg.participants[0],
            provider="meta",
        )
    with pytest.raises(ValueError, match="otro transporte"):
        incoming(client, cfg, "Hola")
    with tenant_context(cfg.tenant_id):
        assert ChannelIdentity.objects.get().provider == "meta"
        assert not TwilioOutbound.objects.exists()


def test_twilio_redaction_removes_queued_copies_and_cancels_jobs(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    from web.conversations.privacy import redact_candidate

    client, cfg = sandbox
    sid = start(client, cfg)
    with tenant_context(cfg.tenant_id):
        app_id = Session.objects.get(pk=sid).application_id
        out_ids = list(TwilioOutbound.objects.values_list("id", flat=True))
    redact_candidate(cfg.tenant_id, app_id, "TEST-TWILIO-ERASURE", apply=True)
    with tenant_context(cfg.tenant_id):
        assert not TwilioOutbound.objects.exclude(text="[contenido retirado]").exists()
        identity = ChannelIdentity.objects.get(provider="twilio")
        assert (
            identity.opted_out
            and identity.onboarding == {}
            and identity.contact.startswith("erased:")
        )
        assert all(not item.payload for item in ChannelInbound.objects.all())
    assert (
        not Job.objects.filter(kind__in=["twilio_send", "twilio_inbound"])
        .exclude(status="cancelled")
        .exists()
    )
    for outbound_id in out_ids:
        assert async_to_sync(claim_send)(cfg.tenant_id, outbound_id, cfg) is None


def test_whatsapp_assessment_shows_in_normal_dashboard(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    from screening.platform.container import get_container

    client, cfg = sandbox
    sid = start(client, cfg)
    incoming(client, cfg, "Tengo tres años de experiencia en cocina y disponibilidad completa.")
    container = get_container()
    session = async_to_sync(container.conversations.repo.get_session)(cfg.tenant_id, sid)
    # The existing evaluator is deterministic here; no claim of a live model/provider test.
    outcome = async_to_sync(container.assessment.complete)(session.run_context)
    assert outcome["verdict"] == "review"
    detail = client.get(f"/api/v1/tenants/{cfg.tenant_id}/workspace/{sid}").json()
    assert detail["summary"] == "Modo de prueba: sin evaluación real."
    assert detail["provisional"] is False and detail["decisions"]
    assert detail["channel"] == "whatsapp" and detail["status"] == "completed"
    assert "__reason" not in detail["fields"]


def test_invalid_config_does_not_expose_secret_in_worker_errors(
    settings: Any,
    tmp_path: Path,
) -> None:
    from web.conversations.twilio_webhook import configuration

    path = tmp_path / "bad-config.json"
    path.write_text('{"unexpected":"SYNTHETIC-SECRET"}')
    settings.TWILIO_CONFIG_FILE = str(path)
    with pytest.raises(ValueError, match="ausente o inválida") as error:
        configuration(uuid4())
    assert "SYNTHETIC-SECRET" not in str(error.value)


def test_previous_application_outbox_is_redacted_after_contact_starts_new_one(
    sandbox: tuple[Client, TwilioConfig],
) -> None:
    from web.conversations.privacy import redact_candidate

    client, cfg = sandbox
    old = start(client, cfg)
    with tenant_context(cfg.tenant_id):
        old_app = Session.objects.get(pk=old).application_id
        Session.objects.filter(pk=old).update(status="completed")
    incoming(client, cfg, "NUEVA")
    incoming(client, cfg, "ACEPTO 1")
    incoming(client, cfg, "Otra Prueba")
    redact_candidate(cfg.tenant_id, old_app, "TEST-OLD-APPLICATION", apply=True)
    with tenant_context(cfg.tenant_id):
        old_out = TwilioOutbound.objects.get(message__session_id=old)
        assert old_out.text == "[contenido retirado]" and old_out.state == "blocked"
        identity = ChannelIdentity.objects.get(provider="twilio")
        assert identity.application_id != old_app and not identity.opted_out
