from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from adapters.connectors_registry import DefaultConnectorRegistry
from adapters.persistence.channels import DjangoChannelStore
from adapters.privacy import pseudonymize
from screening.domain.channels import DeliveryReceipt, normalize_contact, template_values
from screening.domain.errors import NotAllowed, NotFound
from screening.domain.profile import Connection
from screening.platform.container import get_container
from tests.api.test_phone_api import phone, send
from web.accounts.models import User
from web.conversations.meta_webhook import apply_status
from web.conversations.models import Application, ChannelDelivery, Event, Message, Session
from web.decisions.models import Decision, Result
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def test_personal_identifiers_are_not_sent_to_quality_models() -> None:
    text = pseudonymize("Ana Pérez, Ana, ana@example.test +34 600 123 456 y 12345678Z", "Ana Pérez")
    assert "Ana" not in text and "Pérez" not in text
    assert "example.test" not in text and "600" not in text and "12345678Z" not in text


def test_normalized_contacts_and_resolved_template_values() -> None:
    assert normalize_contact("0034 600-123-456") == normalize_contact("+34600123456")
    assert template_values(["nombre", "puesto"], name="Ana", role="Reparto", company="Demo") == (
        "Ana",
        "Reparto",
    )
    with pytest.raises(ValueError):
        template_values(["password"], name="Ana", role="Reparto", company="Demo")


def test_simulators_reject_non_test_applications(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    with tenant_context(tenant):
        Application.objects.filter(pk=app).update(is_test=False)
    response = client.post(
        f"/api/v1/tenants/{tenant}/phone/{app}/messages",
        {"text": "No permitido", "client_message_id": str(app)},
        content_type="application/json",
    )
    assert response.status_code == 404
    with tenant_context(tenant):
        assert not Session.objects.filter(application_id=app).exists()


def test_empty_scope_never_means_all(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    user = User.objects.get(email="reclutadora@orbio.local")
    Membership.objects.filter(user=user, tenant_id=tenant).update(allowed_segments=[])
    client.force_login(user)
    assert client.get(f"/api/v1/tenants/{tenant}/candidates").json()["total"] == 0
    assert client.get(f"/api/v1/tenants/{tenant}/analytics?group=metrics").json()["total"] == 0


def test_delivery_before_provider_ack_is_reconciled(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    apply_status(tenant, "provider-early", "delivered")
    with tenant_context(tenant):
        assert ChannelDelivery.objects.get(provider_id="provider-early").status == "delivered"
        message = Message.objects.filter(session__application_id=app, direction="out").first()
        assert message is not None
    async_to_sync(DjangoChannelStore().delivered)(
        tenant, message.id, DeliveryReceipt("provider-early", "sent", get_container().clock.now())
    )
    with tenant_context(tenant):
        assert Message.objects.get(pk=message.id).delivery_status == "delivered"


def test_metrics_and_human_agreement_exclude_unreviewed(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    user = User.objects.get(email="reclutadora@orbio.local")
    with tenant_context(tenant):
        session = Session.objects.get(application_id=app)
        session.status = "completed"
        session.ended_at = session.created_at + timedelta(minutes=5)
        session.save()
        Result.objects.create(
            tenant_id=tenant,
            session=session,
            verdict="qualified",
            summary="",
            evaluator_model="fake",
            fields={"local_asignado": {"value": "Ruzafa"}, "turno": {"value": "tarde"}},
        )
        Decision.objects.create(
            tenant_id=tenant,
            session=session,
            decision_type="qualify",
            status="applied",
            autonomy_level=1,
            decided_by="agent",
            user=user,
        )
        Decision.objects.create(
            tenant_id=tenant,
            session=session,
            decision_type="qualify",
            status="applied",
            autonomy_level=2,
            decided_by="agent",
        )
    base = f"/api/v1/tenants/{tenant}"
    metrics = client.get(base + "/analytics?group=metrics").json()
    assert metrics["completion_pct"] == 100 and metrics["mean_duration_seconds"] == 300
    assert client.get(base + "/analytics?group=agreement").json()["items"][0]["reviewed"] == 1
    assert (
        client.get(base + "/candidates?verdict=qualified&location=Ruzafa&shift=tarde").json()[
            "total"
        ]
        == 1
    )
    assert client.get(base + "/candidates?location=Castellón").json()["total"] == 0


def test_metrics_response_latency_abandonment_and_filters(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    send(client, tenant, app, "Hola")
    with tenant_context(tenant):
        session = Session.objects.get(application_id=app)
        incoming = Message.objects.filter(session=session, direction="in").first()
        assert incoming is not None
        Message.objects.filter(session=session, direction="out").update(
            created_at=incoming.created_at + timedelta(seconds=3)
        )
        session.status = "abandoned"
        session.save(update_fields=["status"])
        Event.objects.create(
            tenant_id=tenant,
            session=session,
            type="followups_exhausted",
            payload={"stage": "awaiting_candidate"},
        )
    base = f"/api/v1/tenants/{tenant}/analytics?group=metrics"
    data = client.get(base).json()
    assert data["mean_first_response_seconds"] == 3
    assert data["first_response_samples"] == 1
    assert data["abandonment_stages"] == [{"label": "awaiting_candidate", "count": 1}]
    for filter_query in ["&channel=voice", "&segment=not-authorized", "&since=2099-01-01"]:
        filtered = client.get(base + filter_query).json()
        assert filtered["total"] == 0 and filtered["mean_first_response_seconds"] is None
        assert filtered["abandonment_stages"] == []


def test_mcp_destination_cannot_be_chosen_arbitrarily(settings: Any) -> None:
    settings.MCP_ALLOWED_URLS = ["https://allowed.example/mcp"]
    connection = Connection(
        id="test",
        name="test",
        kind="registry",
        system="test",
        transport="mcp",
        settings={"url": "http://169.254.169.254/latest/meta-data", "tool": "anything"},
    )
    with pytest.raises(NotFound, match="no autorizada"):
        DefaultConnectorRegistry(get_container().clock).get("test", connection)


@pytest.mark.parametrize(("slug", "allowed"), [("cliente", True), ("otro_cliente", False)])
def test_local_registry_placeholder_is_bound_to_the_tenant_slug(
    settings: Any, slug: str, allowed: bool
) -> None:
    settings.MCP_ALLOWED_URLS = ["http://mcp-registro:8100/mcp"]
    connection = Connection(
        id="registro",
        name="Registro",
        kind="registry",
        system="registro",
        transport="mcp",
        settings={"url": "http://mcp-registro:8100/mcp", "tool": "verify"},
        secret_ref="secret://cliente/registro",
    )
    registry = DefaultConnectorRegistry(get_container().clock)
    if allowed:
        assert registry.get(slug, connection).unauthenticated_local is True
    else:
        # Another tenant's reference is never treated as the local placeholder.
        with pytest.raises(NotFound, match="no admite autenticación"):
            registry.get(slug, connection)


def test_failed_faq_preparation_cannot_activate_or_move_the_draft(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, tenant, _ = phone
    profiles = get_container().profiles
    active = profiles.active_version(tenant)
    assert active
    user = User.objects.get(email="fde@orbio.local")
    draft = profiles.get_draft(tenant)
    draft.content.brief.voice += " Mensaje breve."
    profiles.save_draft(tenant, draft.rev, draft.content, user.id)

    def fail(*args: Any) -> None:
        raise NotAllowed("Índice no disponible")

    monkeypatch.setattr(profiles, "prepare_version", fail)
    with pytest.raises(NotAllowed, match="Índice"):
        profiles.publish(tenant, "must fail atomically", 100, user.id)
    assert profiles.active_version(tenant).id == active.id
    assert profiles.get_draft(tenant).content.brief.voice.endswith("Mensaje breve.")


def test_voice_requires_explicit_consent(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, app = phone
    token = async_to_sync(get_container().conversations.repo.get_or_create_link)(tenant, app)
    opened = async_to_sync(get_container().conversations.open_session)(token)
    response = client.post(
        f"/api/v1/chat/sessions/{opened.session_id}/voice",
        {"token": token},
        content_type="application/json",
    )
    assert response.status_code == 422
