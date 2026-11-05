from io import StringIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.test import Client

from screening.platform.container import get_container
from screening.platform.voice_runtime import agent_registry_key
from web.accounts.models import User
from web.conversations.models import Application, Event, Message, Session
from web.jobs.models import Job
from web.profiles.models import Publication
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership, Tenant

pytestmark = pytest.mark.django_db(transaction=True)
FIXTURE = Path("seeds/tenants/grupo_sazon/workspace-example.json")
# Fit from the offer criteria: carnet 4, local 3, incorporación 2, experiencia >= 1 año 1.
# A score needs every criterion known; requirements are judged on the required ones only.
FIT: dict[str, tuple[int | None, str]] = {
    "Lucía Ferrer Pons": (100, "met"),
    "Emily Carter": (None, "met"),  # experience not stated: coverage, not a guess
    "Andrés Gil Navarro": (None, "not_met"),
    "Javier Ortega Ruiz": (None, "pending"),
}
ENTRY = "/api/v1/apply/grupo_sazon/repartidores"


@pytest.fixture
def workspace(client: Client) -> tuple[Client, UUID]:
    call_command("seed_local", stdout=StringIO())
    load()
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    return client, Tenant.objects.get(slug="grupo_sazon").id


def load() -> None:
    call_command(
        "load_workspace_example",
        FIXTURE,
        tenant="grupo_sazon",
        actor="fde@orbio.local",
        apply=True,
        stdout=StringIO(),
    )


def application(client: Client, url: str = ENTRY, **extra: Any) -> Any:
    return client.post(
        url,
        {"request_id": str(uuid4()), "name": "Alex Prueba", "consent": True, **extra},
        content_type="application/json",
    )


def test_rider_fixture_dashboard_evaluations_and_idempotent_load(
    workspace: tuple[Client, UUID],
) -> None:
    client, tenant = workspace
    base = f"/api/v1/tenants/{tenant}"
    before = client.get(base + "/workspace").json()
    assert before["applications_count"] == 9
    assert before["awaiting_start_count"] == 1
    assert before["total"] == 8  # The default queue lists started conversations.
    assert len(before["rows"]) == 8
    assert all(not row["is_simulation"] for row in before["rows"])
    assert [o["key"] for o in client.get(base + "/offers").json()["offers"]] == ["repartidores"]
    assert all(o["application_url"] for o in client.get(base + "/offers").json()["offers"])
    agenda = client.get(base + "/interviews?include_past=true").json()
    booked = {row["candidate_name"]: row for row in agenda}
    assert set(booked) == {"Lucía Ferrer Pons", "Emily Carter"}
    lucia, emily = booked["Lucía Ferrer Pons"], booked["Emily Carter"]
    assert lucia["site_ref"] == "VLC-RUZ" and lucia["starts_at_local"].startswith("jueves")
    assert "17:00" in lucia["starts_at_local"]
    assert emily["site_timezone"] == "America/Mexico_City" and "12:00" in emily["starts_at_local"]
    assert all(row["status"] == "booked" and row["reminder_at"] is None for row in agenda)
    assert {row["reminder_status"] for row in agenda} == {"not_scheduled"}
    for row in before["rows"]:
        detail = client.get(base + f"/workspace/{row['id']}").json()
        if row["name"] in FIT:
            fit = detail["fit"]
            assert (fit["score"], fit["requirements"]) == FIT[row["name"]], detail
        assert detail["messages"]
    with tenant_context(tenant):
        sessions = Session.objects.count()
        events = Event.objects.count()
    jobs = Job.objects.count()
    load()
    with tenant_context(tenant):
        assert Session.objects.count() == sessions
        assert Event.objects.count() == events
    assert Job.objects.count() == jobs
    assert client.get(base + "/workspace").json() == before


def test_reusable_offer_creates_new_applications_retry_does_not(
    workspace: tuple[Client, UUID],
) -> None:
    _, tenant = workspace
    anonymous = Client()
    with tenant_context(tenant):
        count = Application.objects.count()
    preview = anonymous.get(ENTRY)
    assert preview.status_code == 200
    assert set(preview.json()) == {"company", "title", "description", "dimensions"}
    assert "policy" not in preview.content.decode()
    with tenant_context(tenant):
        assert Application.objects.count() == count
    key = str(uuid4())
    first = application(anonymous, request_id=key)
    assert first.status_code == 200, first.content
    assert application(anonymous, request_id=key).json() == first.json()
    assert application(anonymous, request_id=key, name="Otra Persona").status_code == 409
    second = application(anonymous)
    assert second.status_code == 200
    assert second.json()["session_id"] != first.json()["session_id"]
    assert second.json()["candidate_link"] != first.json()["candidate_link"]
    with tenant_context(tenant):
        assert Application.objects.count() == count + 2
        session = Session.objects.select_related("application").get(pk=first.json()["session_id"])
        assert session.run_context["offer_key"] == "repartidores"
        assert "Repartidor/a" in session.system_prompt
        assert session.application.is_test and not session.application.is_simulation
    assert application(anonymous, consent=False).status_code == 400
    assert application(anonymous, name=" ").status_code == 400


def test_fresh_chat_handoff_recruiter_reply_and_return(workspace: tuple[Client, UUID]) -> None:
    recruiter, tenant = workspace
    created = application(Client()).json()
    sid = UUID(created["session_id"])
    service = get_container().conversations
    opened = async_to_sync(service.open_session)(created["candidate_link"].rsplit("/", 1)[1])
    assert opened.session_id == sid
    async_to_sync(service.handle_message)(tenant, sid, "handoff", "persona")
    url = f"/api/v1/tenants/{tenant}/sessions/{sid}/human"
    assert (
        recruiter.post(url, {"action": "claim"}, content_type="application/json").status_code == 200
    )
    incoming = async_to_sync(service.handle_message)(
        tenant, sid, "answer", "Me interesa repartir en Ruzafa"
    )
    assert incoming.message and incoming.message.direction == "in"
    reply = recruiter.post(
        url,
        {
            "action": "message",
            "text": "Hola, soy Marta de selección. Te atiendo yo.",
            "message_id": str(uuid4()),
        },
        content_type="application/json",
    )
    assert reply.status_code == 200, reply.content
    with tenant_context(tenant):
        assert Message.objects.filter(session_id=sid, channel="human").count() == 1
    assert (
        recruiter.post(url, {"action": "resume_ai"}, content_type="application/json").json()[
            "status"
        ]
        == "active"
    )
    history = async_to_sync(service.repo.load_history)(tenant, sid)
    assert any("Marta de selección" in str(m.content) for m in history)
    assert recruiter.get(f"/api/v1/tenants/{tenant}/workspace/{sid}").status_code == 200


def test_closed_offer_cannot_start_but_private_chat_stays_valid(
    workspace: tuple[Client, UUID],
) -> None:
    _, tenant = workspace
    created = application(Client()).json()
    with tenant_context(tenant):
        publication = Publication.objects.select_related("version").get(state="active", segment="")
        version = publication.version
        # Test fixture bypasses immutable-version triggers only through a new proper version.
        from adapters.persistence.profiles import DjangoProfileRepository
        from screening.domain.profile import ProfileContent

        profile = ProfileContent.model_validate(version.content)
        profile.offers[0].status = "closed"
        repo = DjangoProfileRepository()
        actor = User.objects.get(email="fde@orbio.local").id
        updated = repo.create_version(tenant, profile, "medium", "Cierre de oferta", actor)
        next_publication = repo.create_publication(
            tenant, updated.id, version.id, 100, False, False, "Cierre", actor
        )
        repo.activate(tenant, next_publication.id)
    assert Client().get(ENTRY).status_code == 410
    assert application(Client()).status_code == 410
    opened = async_to_sync(get_container().conversations.open_session)(
        created["candidate_link"].rsplit("/", 1)[1]
    )
    assert str(opened.session_id) == created["session_id"]
    assert Client().get("/api/v1/apply/logistica/repartidores").status_code == 404


def test_voice_policy_registry_is_distinct_per_offer(workspace: tuple[Client, UUID]) -> None:
    _, tenant = workspace
    with tenant_context(tenant):
        from adapters.persistence.profiles import DjangoProfileRepository
        from screening.domain.profile import ProfileContent

        version = Publication.objects.select_related("version").get(state="active", segment="")
        profile = ProfileContent.model_validate(version.version.content)
        profile.offers.append(
            profile.offers[0].model_copy(update={"key": "repartidores-mx", "title": "Reparto MX"})
        )
        repo = DjangoProfileRepository()
        actor = User.objects.get(email="fde@orbio.local").id
        updated = repo.create_version(tenant, profile, "medium", "Oferta México", actor)
        publication = repo.create_publication(
            tenant, updated.id, version.version_id, 100, False, False, "MX", actor
        )
        repo.activate(tenant, publication.id)
    one = application(Client()).json()
    two = application(Client(), "/api/v1/apply/grupo_sazon/repartidores-mx").json()
    repo_ = get_container().conversations.repo
    a = async_to_sync(repo_.get_session)(tenant, UUID(one["session_id"]))
    b = async_to_sync(repo_.get_session)(tenant, UUID(two["session_id"]))
    assert a.profile_version_id == b.profile_version_id
    assert agent_registry_key(a) != agent_registry_key(b)
    assert agent_registry_key(a).endswith(":repartidores")


def test_local_recruiter_access_is_limited_to_the_new_fixture(client: Client) -> None:
    call_command("seed_local", stdout=StringIO())
    tenant = Tenant.objects.get(slug="grupo_sazon")
    member = Membership.objects.get(tenant=tenant, user__email="reclutadora@orbio.local")
    member.allowed_segments = ["existing-scope"]
    member.save(update_fields=["allowed_segments"])
    load()
    member.refresh_from_db()
    assert member.allowed_segments == ["existing-scope", "sazon-repartidores"]
    client.force_login(member.user)
    assert client.get(f"/api/v1/tenants/{tenant.id}/workspace").json()["applications_count"] == 9
    fresh = application(Client()).json()
    assert (
        client.get(f"/api/v1/tenants/{tenant.id}/workspace/{fresh['session_id']}").status_code
        == 200
    )
    member.allowed_segments = ["unrelated"]
    member.save(update_fields=["allowed_segments"])
    assert (
        client.get(f"/api/v1/tenants/{tenant.id}/workspace/{fresh['session_id']}").status_code
        == 404
    )
