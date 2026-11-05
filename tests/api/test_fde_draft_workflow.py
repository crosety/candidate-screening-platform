from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.conversations.models import Offer, Session
from web.tenancy.context import tenant_context

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def definition(key: str = "cook") -> dict[str, Any]:
    return {
        "key": key,
        "title": "Cocina",
        "status": "draft",
        "description": "Dos años de experiencia profesional en cocina.",
        "instructions": "Pregunta por experiencia y disponibilidad.",
        "fields": [
            {
                "key": "years",
                "description": "Años en cocina",
                "label": "Experiencia",
                "value_type": "number",
                "ats_field": "custom.years",
                "required": True,
            }
        ],
        "criteria": [
            {
                "key": "experience",
                "label": "Experiencia mínima",
                "field": "years",
                "operator": "minimum",
                "expected": 2,
                "required": True,
                "weight": 3,
            }
        ],
        "dimensions": {"Centro": "Ruzafa"},
        "capabilities": ["handoff.request"],
    }


def test_create_precondition_rejects_existing_offer_without_changing_draft(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    base = f"/api/v1/tenants/{tenant}"
    rev = client.get(base + "/offers").json()["rev"]
    first = client.put(
        base + "/offers/cook",
        {"rev": rev, "definition": definition()},
        content_type="application/json",
        HTTP_IF_NONE_MATCH="*",
    )
    assert first.status_code == 200, first.content
    before = client.get(base + "/draft").json()
    duplicate = client.put(
        base + "/offers/cook",
        {"rev": before["rev"], "definition": {**definition(), "title": "No reemplazar"}},
        content_type="application/json",
        HTTP_IF_NONE_MATCH="*",
    )
    assert duplicate.status_code == 412, duplicate.content
    assert client.get(base + "/draft").json() == before
    # Explicit editing keeps its existing PUT contract.
    edit = client.put(
        base + "/offers/cook",
        {"rev": before["rev"], "definition": {**definition(), "title": "Cocina actualizada"}},
        content_type="application/json",
    )
    assert edit.status_code == 200
    assert (
        next(
            o for o in client.get(base + "/draft").json()["content"]["offers"] if o["key"] == "cook"
        )["title"]
        == "Cocina actualizada"
    )


def test_create_precondition_also_protects_legacy_offer_identity(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    base = f"/api/v1/tenants/{tenant}"
    with tenant_context(tenant):
        Offer.objects.get_or_create(tenant_id=tenant, key="legacy-offer")
    before = client.get(base + "/draft").json()
    response = client.put(
        base + "/offers/legacy-offer",
        {"rev": before["rev"], "definition": definition("legacy-offer")},
        content_type="application/json",
        HTTP_IF_NONE_MATCH="*",
    )
    assert response.status_code == 412
    assert client.get(base + "/draft").json() == before


def test_multiple_offer_drafts_roundtrip_and_share_profile_conflict_protection(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, app = phone
    base = f"/api/v1/tenants/{tenant}"
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    opened = async_to_sync(service.open_session)(token)
    with tenant_context(tenant):
        original = Session.objects.get(id=opened.session_id)
        snapshot = (original.profile_version_id, original.run_context, original.system_prompt)
    before = client.get(base + "/draft").json()
    versions = client.get(base + "/versions").json()
    first = client.put(
        base + "/offers/cook",
        {"rev": before["rev"], "definition": definition()},
        content_type="application/json",
        HTTP_IF_NONE_MATCH="*",
    )
    assert first.status_code == 200
    # A profile editor loaded before the offer edit cannot erase the new offer.
    before["content"]["brief"]["voice"] += " Edición desactualizada."
    conflict = client.put(base + "/draft", before, content_type="application/json")
    assert conflict.status_code == 409
    fresh = client.get(base + "/draft").json()
    fresh["content"]["brief"]["voice"] += " Un siguiente paso claro."
    saved = client.put(base + "/draft", fresh, content_type="application/json")
    assert saved.status_code == 200
    # The inverse conflict also rejects an offer editor loaded before a profile edit.
    second = {**definition("waiter"), "title": "Sala"}
    stale = client.put(
        base + "/offers/waiter",
        {"rev": fresh["rev"], "definition": second},
        content_type="application/json",
        HTTP_IF_NONE_MATCH="*",
    )
    assert stale.status_code == 409
    created = client.put(
        base + "/offers/waiter",
        {"rev": saved.json()["rev"], "definition": second},
        content_type="application/json",
        HTTP_IF_NONE_MATCH="*",
    )
    assert created.status_code == 200
    persisted = client.get(base + "/offers").json()
    for expected in [definition(), second]:
        row = next(o for o in persisted["offers"] if o["key"] == expected["key"])
        assert row["published"] is None and row["application_url"] is None
        for key, value in expected.items():
            if key not in ("fields", "criteria"):
                assert row["draft"][key] == value
        for key in ("fields", "criteria"):
            for name, value in expected[key][0].items():
                assert row["draft"][key][0][name] == value
    assert client.get(base + "/versions").json() == versions
    with tenant_context(tenant):
        original.refresh_from_db()
        assert (
            original.profile_version_id,
            original.run_context,
            original.system_prompt,
        ) == snapshot


def test_invalid_offer_never_partially_writes_or_creates_an_identity(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    base = f"/api/v1/tenants/{tenant}"
    before = client.get(base + "/draft").json()
    invalid = deepcopy(definition("invalid"))
    invalid["criteria"][0]["field"] = "missing_field"
    response = client.put(
        base + "/offers/invalid",
        {"rev": before["rev"], "definition": invalid},
        content_type="application/json",
        HTTP_IF_NONE_MATCH="*",
    )
    assert response.status_code == 400
    assert client.get(base + "/draft").json() == before
    with tenant_context(tenant):
        assert not Offer.objects.filter(tenant_id=tenant, key="invalid").exists()
