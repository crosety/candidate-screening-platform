from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from screening.application.ports import NewApplication
from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.accounts.models import User
from web.conversations.models import Event, Message, Offer, Session
from web.decisions.models import Decision
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def test_offer_preview_uses_draft_without_publishing_and_is_fde_only(phone):
    client, tenant, _ = phone
    base = f"/api/v1/tenants/{tenant}"
    data = client.get(base + "/offers").json()
    definition = {
        "key": "cook",
        "title": "Cocinero",
        "fields_mode": "custom",
        "fields": [],
        "source_ids": [],
        "capabilities": [],
    }
    response = client.post(
        base + "/offers/preview",
        {"rev": data["rev"], "definition": definition},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["fields"] == response.json()["sources"] == []
    assert response.json()["fields_origin"] == "custom"
    assert client.get(base + "/offers").json()["rev"] == data["rev"]
    invalid = {**definition, "source_ids": ["missing"]}
    assert (
        client.post(
            base + "/offers/preview",
            {"rev": data["rev"], "definition": invalid},
            content_type="application/json",
        ).status_code
        == 400
    )
    assert (
        client.post(
            base + "/offers/preview",
            {"rev": data["rev"] + 1, "definition": definition},
            content_type="application/json",
        ).status_code
        == 409
    )
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert (
        client.post(
            base + "/offers/preview",
            {"rev": data["rev"], "definition": definition},
            content_type="application/json",
        ).status_code
        == 403
    )


def test_common_draft_changes_are_visible_on_offer_cards(phone):
    client, tenant, _ = phone
    base = f"/api/v1/tenants/{tenant}"
    before = client.get(base + "/offers").json()
    assert before["common_changes"] is False
    draft = client.get(base + "/draft").json()
    draft["content"]["brief"]["voice"] += " Política compartida actualizada."
    response = client.put(base + "/draft", draft, content_type="application/json")
    assert response.status_code == 200, response.content
    after = client.get(base + "/offers").json()
    assert after["common_changes"] is True
    assert all(o["has_changes"] for o in after["offers"])


def test_preview_shows_legacy_search_sources_even_without_context_flag(phone):
    client, tenant, _ = phone
    base = f"/api/v1/tenants/{tenant}"
    draft = client.get(base + "/draft").json()
    draft["content"]["sources"] = [
        {
            "id": "search",
            "name": "Consultable",
            "kind": "faq",
            "text": "Contenido",
            "mode": "search",
            "in_context": False,
        },
        {
            "id": "unused",
            "name": "Inactivo",
            "kind": "faq",
            "text": "No usar",
            "mode": "context",
            "in_context": False,
        },
    ]
    saved = client.put(base + "/draft", draft, content_type="application/json")
    assert saved.status_code == 200, saved.content
    preview = client.post(
        base + "/offers/preview",
        {"rev": saved.json()["rev"], "definition": {"key": "a", "title": "A"}},
        content_type="application/json",
    )
    assert preview.status_code == 200, preview.content
    assert [s["id"] for s in preview.json()["sources"]] == ["search"]


def test_operational_queue_excludes_simulations_and_includes_handoffs(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, app = phone
    service = get_container().conversations
    test_token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    simulated = async_to_sync(service.open_session)(test_token)
    _, url = async_to_sync(service.ingest)(
        "grupo_sazon",
        NewApplication("operational", "Lucía", "+34000000000", "Valencia", "repartidores"),
    )
    actual = async_to_sync(service.open_session)(url.rsplit("/", 1)[1])
    with tenant_context(tenant):
        Session.objects.filter(pk=actual.session_id).update(status="handed_off")
        Message.objects.create(
            tenant_id=tenant,
            session_id=actual.session_id,
            direction="in",
            text="Quiero hablar con una persona",
            channel="web",
        )
        Event.objects.create(
            tenant_id=tenant,
            session_id=actual.session_id,
            type="handoff_requested",
            payload={"reason": "keyword"},
        )
        Decision.objects.create(
            tenant_id=tenant,
            session_id=simulated.session_id,
            decision_type="review",
            status="proposed",
            autonomy_level=1,
            decided_by="agent",
        )
    base = f"/api/v1/tenants/{tenant}"
    response = client.get(base + "/workspace?attention=true")
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["total"] == data["attention_count"] == data["applications_count"] == 1
    assert data["rows"][0]["id"] == str(actual.session_id)
    assert data["rows"][0]["attention"] == ["human"]
    assert client.get(base + "/workspace?environment=simulation").json()["total"] == 1
    detail = client.get(base + f"/workspace/{actual.session_id}")
    assert detail.status_code == 200, detail.content
    assert detail.json()["phone"] == "+34000000000"
    assert detail.json()["handed_off_at"]
    assert detail.json()["provisional"] is True
    assert detail.json()["fit"]["score"] is None
    with tenant_context(tenant):
        Event.objects.create(tenant_id=tenant, session_id=actual.session_id, type="human_claim")
    assert (
        client.get(base + f"/workspace/{actual.session_id}").json()["handed_off_at"]
        == detail.json()["handed_off_at"]
    )
    assert client.get(base + "/workspace?offset=-1").status_code == 400
    _, _ = async_to_sync(service.ingest)(
        "grupo_sazon",
        NewApplication("not-started", "Aún sin iniciar", "", "Valencia", "repartidores"),
    )
    waiting = client.get(base + "/workspace?status=awaiting").json()
    assert waiting["total"] == 1 and waiting["awaiting"][0]["name"] == "Aún sin iniciar"
    assert client.get(base + "/workspace?status=awaiting&attention=true").json()["total"] == 0
    member = Membership.objects.get(tenant_id=tenant, user__email="reclutadora@orbio.local")
    member.allowed_segments = ["other"]
    member.save()
    client.force_login(member.user)
    assert client.get(base + "/workspace").json()["total"] == 0
    assert client.get(base + f"/workspace/{actual.session_id}").status_code == 404


def test_offer_edit_is_draft_only_with_optimistic_lock_and_recruiter_read_only(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    base = f"/api/v1/tenants/{tenant}"
    data = client.get(base + "/offers").json()
    definition = {
        "key": "cook",
        "title": "Cocinero",
        "status": "open",
        "description": "Cocina y turnos",
    }
    response = client.put(
        base + "/offers/cook",
        {"rev": data["rev"], "definition": definition},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    item = next(o for o in response.json()["offers"] if o["key"] == "cook")
    assert item["draft"]["status"] == "open" and item["published"] is None
    assert item["has_changes"] is True
    with tenant_context(tenant):
        assert Offer.objects.filter(tenant_id=tenant, key="cook").exists()
    assert (
        client.put(
            base + "/offers/cook",
            {"rev": data["rev"], "definition": definition},
            content_type="application/json",
        ).status_code
        == 409
    )
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert (
        client.put(
            base + "/offers/cook",
            {"rev": response.json()["rev"], "definition": definition},
            content_type="application/json",
        ).status_code
        == 403
    )
    assert all(o["key"] != "cook" for o in client.get(base + "/offers").json()["offers"])


def test_offers_hide_unconfigured_references_without_real_candidates(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    service = get_container().conversations
    _, _ = async_to_sync(service.ingest)(
        "grupo_sazon", NewApplication("legacy-real", "Ana", "", "Valencia", "REP-OLD")
    )
    with tenant_context(tenant):
        Offer.objects.get_or_create(tenant_id=tenant, key="only-tests")
    keys = {o["key"]: o for o in client.get(f"/api/v1/tenants/{tenant}/offers").json()["offers"]}
    assert "only-tests" not in keys
    assert keys["REP-OLD"]["status"] == "legacy" and keys["REP-OLD"]["applications"] == 1


def test_snapshot_evidence_never_becomes_final_and_is_deduplicated(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, app = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    opened = async_to_sync(service.open_session)(token)
    with tenant_context(tenant):
        Message.objects.create(
            tenant_id=tenant,
            session_id=opened.session_id,
            direction="in",
            channel="web",
            text="Tengo tres años de experiencia",
        )
        Event.objects.create(
            tenant_id=tenant,
            session_id=opened.session_id,
            type="candidate_snapshot",
            payload={
                "fields": {"years": {"value": 20, "evidence": "Inventado", "confidence": "high"}},
                "summary": "Provisional",
                "message_count": 1,
            },
        )
    url = f"/api/v1/tenants/{tenant}/workspace/{opened.session_id}"
    detail = client.get(url).json()
    assert detail["fields"]["years"]["value"] is None
    assert detail["provisional"]
    assert client.post(url + "/snapshot").status_code == 200
    assert client.post(url + "/snapshot").status_code == 200
    from web.jobs.models import Job

    assert Job.objects.filter(tenant_id=tenant, kind="candidate_snapshot").count() == 1
    with tenant_context(tenant):
        Job.objects.filter(tenant_id=tenant, kind="candidate_snapshot").update(status="dead")
    assert client.post(url + "/snapshot").status_code == 409


def test_erased_candidates_are_not_exposed(phone: tuple[Client, UUID, UUID]) -> None:
    from django.utils import timezone

    from web.conversations.models import Application

    client, tenant, app = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    opened = async_to_sync(service.open_session)(token)
    with tenant_context(tenant):
        Application.objects.filter(pk=app).update(erased_at=timezone.now())
    assert client.get(f"/api/v1/tenants/{tenant}/workspace/{opened.session_id}").status_code == 404
    assert (
        client.get(f"/api/v1/tenants/{tenant}/workspace?environment=simulation").json()["total"]
        == 0
    )


def test_historical_simulation_classification_is_enumerated_and_reversible(
    phone: tuple[Client, UUID, UUID],
) -> None:
    import importlib

    from django.apps import apps
    from django.db import connection

    from web.conversations.models import Application

    _, tenant, _ = phone
    service = get_container().conversations
    known, _ = async_to_sync(service.ingest)(
        "grupo_sazon",
        NewApplication("SZN-1001", "Lucía Martín", "+34600000001", "València", "repartidores"),
    )
    unrelated, _ = async_to_sync(service.ingest)(
        "grupo_sazon",
        NewApplication("SIM-unrelated", "Lucía Martín", "", "Valencia", "repartidores"),
    )
    migration = importlib.import_module(
        "web.conversations.migrations.0011_classify_legacy_simulations"
    )
    with connection.schema_editor() as editor:
        migration.classify(apps, editor)
    with tenant_context(tenant):
        assert Application.objects.get(pk=known.id).is_test
        assert not Application.objects.get(pk=unrelated.id).is_test
    with connection.schema_editor() as editor:
        migration.unclassify(apps, editor)
    with tenant_context(tenant):
        assert not Application.objects.get(pk=known.id).is_test
