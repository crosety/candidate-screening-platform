from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.test import Client

from screening.domain.errors import NotAllowed, NotFound
from screening.platform.container import get_container
from tests.api.test_phone_api import phone as phone
from tests.api.test_phone_api import send
from web.conversations.models import ChannelIdentity, Event, Followup, Session
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from worker.handlers import followup_expire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("reply_arrives", [False, True])
def test_expiry_serializes_with_turn_and_rechecks_latest_reply(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch, reply_arrives: bool
) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    proposed = schedule(client, tenant, app)
    with tenant_context(tenant):
        row = Followup.objects.get(pk=proposed["followup_id"])
        row.status = "sent"
        row.save(update_fields=["status"])
    acquired: list[UUID] = []

    class TurnLock:
        @asynccontextmanager
        async def hold(self, tenant_id: UUID, session_id: UUID) -> AsyncIterator[None]:
            assert tenant_id == tenant and session_id == row.session_id
            acquired.append(session_id)
            if reply_arrives:

                @sync_to_async
                def response() -> None:
                    with tenant_context(tenant):
                        ChannelIdentity.objects.filter(application_id=app).update(
                            last_inbound_at=row.last_inbound_at + timedelta(minutes=1)
                        )

                await response()
            yield

    monkeypatch.setattr(get_container().conversations, "turn_lock", TurnLock())
    job = Job(tenant_id=tenant, kind="followup_expire", payload={"followup_id": str(row.id)})
    followup_expire(job)
    followup_expire(job)
    assert len(acquired) == 2
    with tenant_context(tenant):
        session = Session.objects.get(pk=row.session_id)
        assert session.status == ("active" if reply_arrives else "abandoned")
        assert Event.objects.filter(type="followups_exhausted").count() == (
            0 if reply_arrives else 1
        )


def schedule(client: Client, tenant: UUID, app: UUID, hours: int = 2) -> dict[str, str]:
    state = client.get(f"/api/v1/tenants/{tenant}/phone/{app}").json()
    from datetime import datetime

    at = datetime.fromisoformat(state["now"]) + timedelta(hours=hours)
    response = client.post(
        f"/api/v1/tenants/{tenant}/followups",
        {
            "session_id": state["session_id"],
            "at_local": at.isoformat(),
            "reason": f"Retomar en {hours} horas",
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.parametrize("advance_hours", [2, 25])
def test_dial_one_requires_approval_then_phone_clock_dispatches(
    phone: tuple[Client, UUID, UUID],
    advance_hours: int,
) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    with tenant_context(tenant):
        session = Session.objects.get(application_id=app)
        context = dict(session.run_context)
        context["autonomy"] = {**context["autonomy"], "followup": 1}
        session.run_context = context
        session.save(update_fields=["run_context"])
    row = schedule(client, tenant, app)
    assert row["status"] == "proposed"
    assert Job.objects.filter(kind="followup_dispatch").count() == 0
    response = client.post(
        f"/api/v1/tenants/{tenant}/followups/{row['followup_id']}",
        {"action": "approve"},
        content_type="application/json",
    )
    assert response.json()["status"] == "scheduled"
    client.post(
        f"/api/v1/tenants/{tenant}/phone/{app}/advance",
        {"hours": advance_hours},
        content_type="application/json",
    )
    with tenant_context(tenant):
        assert Followup.objects.get(pk=row["followup_id"]).status == "sent"
    messages = client.get(f"/api/v1/tenants/{tenant}/phone/{app}").json()["messages"]
    assert len(messages) == 3 and "Turno programado" in messages[-1]["text"]
    state = client.get(f"/api/v1/tenants/{tenant}/phone/{app}").json()
    assert state["events"][-1]["payload"]["mode"] == ("text" if advance_hours == 2 else "template")


@pytest.mark.parametrize("reply", ["Sí, seguimos", "BAJA", "persona"])
def test_response_opt_out_or_handoff_cancel_jobs(
    phone: tuple[Client, UUID, UUID], reply: str
) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    row = schedule(client, tenant, app)
    client.post(
        f"/api/v1/tenants/{tenant}/followups/{row['followup_id']}",
        {"action": "approve"},
        content_type="application/json",
    )
    send(client, tenant, app, reply)
    with tenant_context(tenant):
        assert Followup.objects.get(pk=row["followup_id"]).status == "cancelled"
    assert Job.objects.get(kind="followup_dispatch").status == "cancelled"


def test_frequency_idempotency_and_cross_tenant(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    one = schedule(client, tenant, app, 2)
    state = client.get(f"/api/v1/tenants/{tenant}/phone/{app}").json()
    session = async_to_sync(get_container().conversations.repo.get_session)(
        tenant, UUID(state["session_id"])
    )
    service = get_container().followups
    identity = async_to_sync(service.store.contact)(session.run_context)
    assert identity is not None
    with pytest.raises(NotAllowed, match="Solo puede haber un"):
        async_to_sync(service.schedule)(
            session.run_context,
            (service.channels.now(identity) + timedelta(hours=4)).isoformat(),
            "Tercero",
        )
    with tenant_context(tenant):
        Followup.objects.filter(pk=one["followup_id"]).update(status="sent")
    two = schedule(client, tenant, app, 3)
    with tenant_context(tenant):
        Followup.objects.filter(pk=two["followup_id"]).update(status="sent")
    three = schedule(client, tenant, app, 4)
    with tenant_context(tenant):
        Followup.objects.filter(pk=three["followup_id"]).update(status="sent")
    with pytest.raises(NotAllowed, match="Máximo tres"):
        async_to_sync(service.schedule)(
            session.run_context,
            (service.channels.now(identity) + timedelta(hours=5)).isoformat(),
            "Cuarto",
        )
    with pytest.raises(NotFound, match="Recordatorio no encontrado"):
        async_to_sync(service.store.get)(uuid4(), UUID(one["followup_id"]))
    forbidden = replace(
        session.run_context, autonomy={**session.run_context.autonomy, "followup": 0}
    )
    assert (
        async_to_sync(service.schedule)(forbidden, "bad", "No permitido")["status"] == "not_allowed"
    )


def test_quiet_hours_are_postponed_and_time_must_be_aware(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    state = client.get(f"/api/v1/tenants/{tenant}/phone/{app}").json()
    response = client.post(
        f"/api/v1/tenants/{tenant}/followups",
        {
            "session_id": state["session_id"],
            "at_local": "2026-10-23T23:00:00+02:00",
            "reason": "Noche",
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["at"] == "2026-10-24T06:00:00+00:00"
    naive = client.post(
        f"/api/v1/tenants/{tenant}/followups",
        {
            "session_id": state["session_id"],
            "at_local": "2026-10-23T15:00:00",
            "reason": "Sin zona",
        },
        content_type="application/json",
    )
    assert naive.status_code == 400
