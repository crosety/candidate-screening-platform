from typing import Any
from uuid import UUID, uuid4

import pytest
from asgiref.sync import async_to_sync
from django.test import Client
from django.utils import timezone

from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.accounts.models import User
from web.conversations.models import Followup, Message, Session
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership, Tenant

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def opened(phone: tuple[Client, UUID, UUID]) -> tuple[Client, UUID, UUID, str]:
    client, tenant, app = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    session = async_to_sync(service.open_session)(token)
    return (
        client,
        tenant,
        session.session_id,
        f"/api/v1/tenants/{tenant}/sessions/{session.session_id}/human",
    )


def post(client: Client, url: str, action: str, **kwargs: Any) -> Any:
    return client.post(url, {"action": action, **kwargs}, content_type="application/json")


def test_handoff_candidate_and_hr_reply_then_resume_and_close(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, sid, url = opened(phone)
    service = get_container().conversations
    async_to_sync(service.handle_message)(tenant, sid, "request", "persona")
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert post(client, url, "message", text="Hi", message_id=str(uuid4())).status_code == 409
    assert post(client, url, "claim").json()["owned_by_me"] is True
    incoming = async_to_sync(service.handle_message)(
        tenant, sid, "candidate", "¿Podemos hablar mañana?"
    )
    assert incoming.message and incoming.message.direction == "in"
    key = str(uuid4())
    data = post(client, url, "message", text="Sí, soy Marta del equipo.", message_id=key)
    assert data.status_code == 200 and data.json()["message"]["channel"] == "human"
    assert (
        post(client, url, "message", text="Sí, soy Marta del equipo.", message_id=key).status_code
        == 200
    )
    with tenant_context(tenant):
        assert Message.objects.filter(session_id=sid, channel="human").count() == 1
    assert post(client, url, "message", text="Different", message_id=key).status_code == 409
    assert post(client, url, "resume_ai").json()["status"] == "active"
    history = async_to_sync(service.repo.load_history)(tenant, sid)
    assert any("Marta" in str(m.content) for m in history)
    assert post(client, url, "claim").status_code == 200
    second = str(uuid4())
    assert (
        post(client, url, "message", text="Segundo episodio", message_id=second).status_code == 200
    )
    assert post(client, url, "resume_ai").status_code == 200
    resumed = async_to_sync(service.repo.load_history)(tenant, sid)
    assert sum("Marta" in str(m.content) for m in resumed) == 1
    assert sum("Segundo episodio" in str(m.content) for m in resumed) == 1
    assert post(client, url, "claim").status_code == 200
    assert post(client, url, "close").json()["status"] == "completed"
    assert post(client, url, "message", text="Too late", message_id=str(uuid4())).status_code == 409
    assert post(client, url, "claim").status_code == 409


def test_hr_scope_and_exclusive_owner(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, sid, url = opened(phone)
    assert post(client, url, "claim").status_code == 200
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert post(client, url, "claim").status_code == 409
    assert post(client, url, "close").status_code == 409
    other = Tenant.objects.get(slug="logistica")
    assert client.get(f"/api/v1/tenants/{other.id}/sessions/{sid}/human").status_code == 404
    Membership.objects.filter(user=client.session["_auth_user_id"], tenant_id=tenant).update(
        allowed_segments=["not-this-segment"]
    )
    assert client.get(url).status_code == 404
    assert post(client, url, "claim").status_code == 404
    with tenant_context(tenant):
        assert Session.objects.get(pk=sid).status == "handed_off"


def test_anonymous_cannot_take_conversation(phone: tuple[Client, UUID, UUID]) -> None:
    client, _, _, url = opened(phone)
    client.logout()
    assert post(client, url, "claim").status_code == 401


def test_hr_claim_cancels_pending_automatic_followups(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, sid, url = opened(phone)
    job = Job.objects.create(tenant_id=tenant, kind="followup_dispatch")
    with tenant_context(tenant):
        reminder = Followup.objects.create(
            tenant_id=tenant,
            session_id=sid,
            contact="synthetic",
            run_at=timezone.now(),
            reason="test",
            status="scheduled",
            simulated=True,
            job_id=job.id,
            idempotency_key=str(uuid4()),
        )
    assert post(client, url, "claim").status_code == 200
    with tenant_context(tenant):
        reminder.refresh_from_db()
    job.refresh_from_db()
    assert reminder.status == job.status == "cancelled"
