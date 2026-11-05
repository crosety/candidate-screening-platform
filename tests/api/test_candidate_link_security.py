from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from adapters.persistence.conversations import DjangoConversationRepository
from screening.application.ports import NewApplication
from screening.domain.errors import NotFound
from web.conversations.links import digest_token
from web.conversations.models import CandidateLink
from web.core.security import consume_socket_ticket, issue_socket_ticket
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


def test_digest_expiry_revocation_and_rotation() -> None:
    tenant = Tenant.objects.create(slug="test", name="Prueba")
    repo = DjangoConversationRepository()
    app = async_to_sync(repo.upsert_application)(
        tenant.id, NewApplication("one", "Prueba", "", "", "")
    )
    token = async_to_sync(repo.get_or_create_link)(tenant.id, app.id)
    link = CandidateLink.objects.get(application_id=app.id)
    assert link.token != token and link.token == digest_token(token)
    assert async_to_sync(repo.resolve_link)(token).application_id == app.id
    assert async_to_sync(repo.get_or_create_link)(tenant.id, app.id) == token
    link.expires_at = timezone.now() - timedelta(seconds=1)
    link.save(update_fields=["expires_at"])
    with pytest.raises(NotFound):
        async_to_sync(repo.resolve_link)(token)
    call_command(
        "candidate_link", tenant=tenant.slug, application=app.id, action="rotate", confirm=True
    )
    replacement = async_to_sync(repo.get_or_create_link)(tenant.id, app.id)
    assert replacement != token
    with pytest.raises(NotFound):
        async_to_sync(repo.resolve_link)(token)
    call_command(
        "candidate_link", tenant=tenant.slug, application=app.id, action="revoke", confirm=True
    )
    with pytest.raises(NotFound):
        async_to_sync(repo.resolve_link)(replacement)


def test_socket_tickets_are_single_use_and_bound_to_session() -> None:
    tenant, session = uuid4(), uuid4()
    ticket = issue_socket_ticket(tenant, session, "digest")
    assert consume_socket_ticket(ticket, session)["tenant_id"] == str(tenant)
    assert consume_socket_ticket(ticket, session) is None
    other = issue_socket_ticket(tenant, session, "digest")
    assert consume_socket_ticket(other, uuid4()) is None


def test_login_attempts_are_bounded_without_exposing_account() -> None:
    client = Client()
    payload = {"email": "unknown@example.test", "password": "incorrect"}
    for _ in range(8):
        assert (
            client.post("/api/v1/auth/login", payload, content_type="application/json").status_code
            == 401
        )
    response = client.post("/api/v1/auth/login", payload, content_type="application/json")
    assert response.status_code == 429
    assert "unknown" not in response.content.decode()
