from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.test import Client

from adapters.fakes.channel import SimulatedWhatsAppAdapter
from adapters.fakes.clock import FixedClock
from screening.application.ports import NewApplication
from screening.domain.channels import ChannelTemplate, NormalizedInbound
from screening.domain.errors import NotAllowed
from screening.platform.container import get_container
from web.accounts.models import User
from web.conversations.meta_webhook import apply_status
from web.conversations.models import ChannelIdentity, Message, ModelMessage, Session
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def phone(client: Client) -> tuple[Client, UUID, UUID]:
    call_command("seed_local")
    client.force_login(User.objects.get(email="fde@orbio.local"))
    tenant = Tenant.objects.get(slug="grupo_sazon")
    container = get_container()
    clock = FixedClock(datetime(2026, 10, 23, 10, tzinfo=UTC))
    container.channels.clock = clock
    assert isinstance(container.channels.adapter, SimulatedWhatsAppAdapter)
    container.channels.adapter.clock = clock
    app, _ = async_to_sync(container.conversations.ingest)(
        tenant.slug,
        NewApplication("phone-demo", "Teléfono Demo", "", "València", "repartidores", is_test=True),
    )
    return client, tenant.id, app.id


def send(client: Client, tenant: UUID, app: UUID, text: str, key: str = "") -> dict[str, Any]:
    result = client.post(
        f"/api/v1/tenants/{tenant}/phone/{app}/messages",
        {"text": text, "client_message_id": key or str(uuid4())},
        content_type="application/json",
    )
    assert result.status_code == 200, result.content
    return result.json()


def test_same_agent_shared_history_idempotency_and_template(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, app = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    web = async_to_sync(service.open_session)(token)
    async_to_sync(service.start)(tenant, web.session_id)
    key = str(uuid4())
    result = send(client, tenant, app, "Hola", key)
    assert result["session_id"] == str(web.session_id)
    assert result["delivery"]["simulated"] is True
    assert send(client, tenant, app, "Hola", key)["status"] == "duplicate"
    url = f"/api/v1/tenants/{tenant}/phone/{app}"
    before = client.get(url).json()
    assert len(before["messages"]) == 3
    response = client.post(url + "/advance", {"hours": 25}, content_type="application/json")
    assert response.status_code == 200
    blocked = client.post(
        url + "/outbound", {"use_template": False}, content_type="application/json"
    )
    assert blocked.json()["status"] == "no_approved_template"
    template = client.post(url + "/outbound", {}, content_type="application/json")
    assert template.json()["status"] == "template"
    assert client.get(url).json()["events"][-1]["payload"]["mode"] == "template"
    with tenant_context(tenant):
        assert Session.objects.filter(application_id=app).count() == 1
        assert ModelMessage.objects.filter(session_id=web.session_id).count() == 4


def test_opt_out_precedes_model_and_blocks_future_sends(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    url = f"/api/v1/tenants/{tenant}/phone/{app}"
    before = client.get(url).json()
    assert send(client, tenant, app, "BAJA")["status"] == "opted_out"
    assert send(client, tenant, app, "Más tarde")["status"] == "opted_out"
    after = client.get(url).json()
    assert len(after["messages"]) == len(before["messages"]) + 1
    assert after["status"] == "abandoned" and after["opted_out"]
    assert (
        client.post(url + "/outbound", {}, content_type="application/json").json()["status"]
        == "opted_out"
    )


def test_phone_scope_clock_and_roles(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    url = f"/api/v1/tenants/{tenant}/phone/{app}"
    client.get(url)
    other = Tenant.objects.get(slug="logistica")
    assert client.get(f"/api/v1/tenants/{other.id}/phone/{app}").status_code == 404
    assert (
        client.post(url + "/advance", {"hours": -1}, content_type="application/json").status_code
        == 400
    )
    with tenant_context(tenant):
        assert ChannelIdentity.objects.get(application_id=app).clock_offset_seconds == 0
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert client.get(url).status_code == 403


def test_unknown_contact_never_opens_session(phone: tuple[Client, UUID, UUID]) -> None:
    _, tenant, _ = phone
    service = get_container().channels
    incoming = NormalizedInbound(
        tenant, "whatsapp", "one", "sim:unknown", "Hola", service.clock.now()
    )
    result = async_to_sync(service.receive)(incoming, simulated=True)
    assert result.status == "unknown_contact" and result.session_id is None
    with tenant_context(tenant):
        assert Session.objects.count() == 0


def test_candidate_code_from_other_tenant_is_rejected(phone: tuple[Client, UUID, UUID]) -> None:
    _, tenant, _ = phone
    container = get_container()
    other = Tenant.objects.get(slug="logistica")
    _, link = async_to_sync(container.conversations.ingest)(
        other.slug, NewApplication("other", "Otra Persona", "", "València", "warehouse")
    )
    incoming = NormalizedInbound(
        tenant,
        "whatsapp",
        "other-code",
        "sim:unknown",
        "Hola",
        container.clock.now(),
        link.rsplit("/", 1)[1],
    )
    with pytest.raises(NotAllowed):
        async_to_sync(container.channels.receive)(incoming, simulated=True)


def test_template_first_contact_opens_only_one_session(phone: tuple[Client, UUID, UUID]) -> None:
    _, tenant, app = phone
    service = get_container().channels
    contact = f"sim:{app}"
    async_to_sync(service.store.bind)(tenant, contact, app, True)
    template = ChannelTemplate("hello_first_contact", "es", "first_contact", approved=True)
    result = async_to_sync(service.proactive)(tenant, contact, "Hola", template, simulated=True)
    assert result.status == "template"
    with tenant_context(tenant):
        assert Session.objects.filter(application_id=app).count() == 1
        assert ModelMessage.objects.count() == 0


def test_delivery_status_does_not_regress_or_cross_tenant(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    with tenant_context(tenant):
        row = Message.objects.get(direction="out")
        row.provider_message_id = "wamid.status"
        row.simulated = False
        row.delivery_status = "sent"
        row.save()
    apply_status(uuid4(), "wamid.status", "read")
    with tenant_context(tenant):
        row.refresh_from_db()
        assert row.delivery_status == "sent"
    apply_status(tenant, "wamid.status", "read")
    apply_status(tenant, "wamid.status", "sent")
    apply_status(tenant, "wamid.status", "failed")
    with tenant_context(tenant):
        row.refresh_from_db()
        assert row.delivery_status == "read"
