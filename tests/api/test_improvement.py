import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from adapters.eval_harness import harness_metadata
from adapters.fakes.copilot import ScriptedCopilot
from adapters.persistence.evals_gate import django_evals_gate
from adapters.persistence.profile_versions import pilot_version
from screening.application.ports import Suggestion
from screening.domain.errors import Conflict, NotAllowed
from screening.domain.eval_identity import eval_snapshot
from screening.domain.profile import ProfileContent
from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.accounts.models import User
from web.conversations.models import Session
from web.evals.models import EvalCase, EvalRun
from web.profiles.improvement import accept, material, propose, store
from web.profiles.maintenance import cancel, reserve
from web.profiles.models import (
    BudgetReservation,
    ImprovementProposal,
    MaintenancePolicy,
    SegmentPilot,
)
from web.tenancy.context import tenant_context

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def test_v3_improvement_rejects_offer_requirements_in_shared_brief(phone, monkeypatch):
    _, tenant, app = phone
    c = get_container()
    user = User.objects.get(email="fde@orbio.local")
    draft = c.profiles.get_draft(tenant)
    content = draft.content.model_copy(deep=True)
    content.schema_version = 3
    content.brief.who = content.brief.fit = ""
    for offer in content.offers:
        offer.fields_mode = "inherit"
        offer.source_ids = [source.id for source in content.sources]
    c.profiles.save_draft(tenant, draft.rev, content, user.id)
    token = async_to_sync(c.conversations.repo.get_or_create_link)(tenant, app)
    opened = async_to_sync(c.conversations.open_session)(token)
    async_to_sync(c.conversations.start)(tenant, opened.session_id)
    observations = async_to_sync(material)(tenant)
    copilot = ScriptedCopilot(
        [
            Suggestion(
                field="who",
                append="Un requisito específico",
                why="Claridad",
                evidence_ids=[observations["evidence"][0]["id"]],
            ),
        ]
    )
    monkeypatch.setattr(c.copilot, "copilot", copilot)
    assert async_to_sync(propose)(tenant, user.id) == []
    context = json.loads(copilot.calls[0][2])
    assert context["allowed_brief_fields"] == ["role", "voice", "followup", "avoid"]
    assert context["offers"][0]["key"] == "repartidores"
    with tenant_context(tenant):
        assert not ImprovementProposal.objects.exists()
        assert not EvalRun.objects.exists()


def proposal(tenant: UUID) -> UUID:
    c = get_container()
    draft = c.profiles.get_draft(tenant)
    content = draft.content.model_copy(deep=True)
    content.brief.voice += " Confirma los datos sin inventar condiciones."
    user = User.objects.get(email="fde@orbio.local")
    return async_to_sync(store)(tenant, user.id, draft.rev, content, "Claridad", [])


def test_alternative_gate_and_acceptance_are_bound_to_exact_content(
    phone: tuple[Client, UUID, UUID],
    settings: Any,
) -> None:
    _, tenant, _ = phone
    c = get_container()
    user = User.objects.get(email="fde@orbio.local")
    pk = proposal(tenant)
    draft = c.profiles.get_draft(tenant)
    with pytest.raises(NotAllowed):
        accept(tenant, pk, user.id)
    with tenant_context(tenant):
        row = ImprovementProposal.objects.get(pk=pk)
        people = c.evals.personas.list_personas("grupo_sazon", "repartidores")
        keys = [p.key for p in people]
        settings.LLM_MODE = "openai"
        run = EvalRun.objects.create(
            tenant_id=tenant,
            draft_rev=draft.rev,
            persona_keys=keys,
            status="done",
            max_cost_usd_micros=100000,
            score_draft=100,
            score_published=90,
            summary={
                "errors": 0,
                "harness": harness_metadata("openai"),
                **eval_snapshot(ProfileContent.model_validate(row.content), people),
                "offer_key": "repartidores",
            },
            alternative_content=row.content,
            created_by=user,
        )
        row.eval_run = run
        row.save()
        for p in people:
            EvalCase.objects.create(
                tenant_id=tenant,
                run=run,
                persona_key=p.key,
                target="draft",
                status="done",
                score=100,
                judge={
                    "verdict": p.expected_verdict,
                    "checks": [
                        {"text": prefix + t, "passed": True}
                        for prefix, texts in (("Debe: ", p.must), ("No debe: ", p.must_not))
                        for t in texts
                    ],
                },
            )
    assert not django_evals_gate(tenant, draft.rev)
    rev = accept(tenant, pk, user.id)
    assert rev == draft.rev + 1 and django_evals_gate(tenant, rev)
    assert c.profiles.active_version(tenant).content != c.profiles.get_draft(tenant).content
    with pytest.raises(NotAllowed):
        accept(tenant, pk, user.id)


def test_stale_proposal_never_overwrites_a_new_revision(phone: tuple[Client, UUID, UUID]) -> None:
    _, tenant, _ = phone
    c = get_container()
    user = User.objects.get(email="fde@orbio.local")
    draft = c.profiles.get_draft(tenant)
    c.profiles.save_draft(tenant, draft.rev, draft.content, user.id)
    with pytest.raises(Conflict):
        async_to_sync(store)(tenant, user.id, draft.rev, draft.content, "stale", [])
    data = async_to_sync(material)(tenant)
    assert "Teléfono Demo" not in str(data)


def test_budget_is_reserved_atomically_idempotent_and_cancellable(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    user = User.objects.get(email="fde@orbio.local")
    now = datetime(2026, 10, 23, 4, tzinfo=UTC)
    assert reserve(tenant, "nightly", "a", now) is None
    with tenant_context(tenant):
        MaintenancePolicy.objects.create(
            tenant_id=tenant,
            nightly_enabled=True,
            daily_budget_micros=200000,
            run_budget_micros=100000,
            updated_by=user,
        )
    first = reserve(tenant, "nightly", "a", now)
    assert first
    assert reserve(tenant, "nightly", "a", now) is None
    assert reserve(tenant, "nightly", "b", now)
    assert reserve(tenant, "nightly", "c", now) is None
    cancel(tenant, first)
    with tenant_context(tenant):
        assert BudgetReservation.objects.get(pk=first).state == "cancelled"
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert client.get(f"/api/v1/tenants/{tenant}/improvements").status_code == 403


def test_pilot_binds_only_matching_segment_and_never_rewrites_sessions(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    c = get_container()
    user = User.objects.get(email="fde@orbio.local")
    active = c.profiles.active_version(tenant)
    assert active
    draft = c.profiles.get_draft(tenant)
    draft.content.connections[0].segments = ["north"]
    draft.content.brief.voice += " Sé breve."
    c.profiles.save_draft(tenant, draft.rev, draft.content, user.id)
    with tenant_context(tenant):
        before = list(Session.objects.values_list("id", "profile_version_id"))
    publication = c.profiles.publish(tenant, "Pilot", 10, user.id, "north")
    assert publication.state == "pending_approval"
    assert async_to_sync(pilot_version)(tenant, uuid4(), "north") is None
    c.profiles.approve(tenant, publication.id, "client", user.id)
    c.profiles.approve(
        tenant, publication.id, "second_fde", User.objects.get(email="fde2@orbio.local").id
    )
    assert c.profiles.active_version(tenant).id == active.id
    with tenant_context(tenant):
        assert list(Session.objects.values_list("id", "profile_version_id")) == before
    selected = [
        async_to_sync(pilot_version)(tenant, UUID(int=i << 96), "north") for i in range(1000)
    ]
    assert selected.count(publication.version_id) == 100
    assert selected.count(active.id) == 900
    assert async_to_sync(pilot_version)(tenant, uuid4(), "south") is None
    data = {
        "segment": "north",
        "version_id": str(uuid4()),
        "baseline_id": str(active.id),
        "percent": 10,
        "enabled": True,
    }
    assert (
        client.put(
            f"/api/v1/tenants/{tenant}/pilots", data, content_type="application/json"
        ).status_code
        == 400
    )
    c.profiles.rollback(tenant, active.id, "Emergency", user.id)
    assert async_to_sync(pilot_version)(tenant, uuid4(), "north") is None
    with tenant_context(tenant):
        assert not SegmentPilot.objects.filter(tenant_id=tenant, enabled=True).exists()
        assert list(Session.objects.values_list("id", "profile_version_id")) == before
