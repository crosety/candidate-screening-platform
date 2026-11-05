from typing import Any
from uuid import UUID

import pytest
from django.test import Client

from adapters.eval_harness import harness_metadata
from adapters.persistence.evals_gate import django_evals_gate
from adapters.personas import FilePersonaSource
from screening.domain.eval_identity import eval_snapshot
from screening.domain.profile import ProfileContent
from screening.platform.container import get_container
from tests.api.test_profiles_api import (
    get_draft,
    publish,
    put_draft,
)
from tests.api.test_profiles_api import (
    profile_client as profile_client,
)
from web.accounts.models import User
from web.evals.models import EvalCase, EvalRun
from web.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db
OFFER = "repartidores"  # The seed profile's only open offer.


@pytest.mark.parametrize(
    "offset,score,full,expected",
    [
        (0, 90, True, 200),
        (-1, 90, True, 400),
        (0, 50, True, 400),
        (0, 90, False, 400),
    ],
)
def test_gate_requires_current_passing_complete_evals(
    profile_client: tuple[Client, str],
    settings: Any,
    offset: int,
    score: int,
    full: bool,
    expected: int,
) -> None:
    client, tenant = profile_client
    settings.PUBLISH_REQUIRES_EVALS = True
    get_container.cache_clear()
    draft = get_draft(client, tenant)
    draft["content"]["brief"]["fit"] += " Una frase."
    assert put_draft(client, tenant, draft).status_code == 200
    assert publish(client, tenant).json()["detail"] == "Faltan evals aprobadas para este borrador."
    current = get_draft(client, tenant)
    people = FilePersonaSource(settings.SEEDS_DIR).list_personas("grupo_sazon", OFFER)
    keys = [p.key for p in people]
    settings.LLM_MODE = "openai"
    with tenant_context(UUID(tenant)):
        run = EvalRun.objects.create(
            tenant_id=tenant,
            draft_rev=current["rev"] + offset,
            persona_keys=keys if full else keys[:1],
            status="done",
            score_draft=score,
            max_cost_usd_micros=1000000,
            created_by=User.objects.get(email="fde@orbio.local"),
            summary={
                "harness": harness_metadata("openai"),
                **eval_snapshot(ProfileContent.model_validate(current["content"]), people),
                "offer_key": OFFER,
            },
        )
        for p in people if full else people[:1]:
            EvalCase.objects.create(
                tenant_id=tenant,
                run=run,
                persona_key=p.key,
                target="draft",
                status="done",
                score=score,
                judge={
                    "verdict": p.expected_verdict,
                    "checks": [
                        {"text": prefix + t, "passed": True}
                        for prefix, texts in (("Debe: ", p.must), ("No debe: ", p.must_not))
                        for t in texts
                    ],
                },
            )
    assert publish(client, tenant).status_code == expected


@pytest.mark.parametrize("new_status", ["queued", "running", "failed", "cancelled", "done"])
def test_newer_incomplete_or_failed_run_invalidates_an_older_pass(
    profile_client: tuple[Client, str],
    settings: Any,
    new_status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, tenant = profile_client
    settings.PUBLISH_REQUIRES_EVALS = True
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_container.cache_clear()
    current = get_draft(client, tenant)
    current["content"]["brief"]["fit"] += " Cambio para revisar la puerta."
    assert put_draft(client, tenant, current).status_code == 200
    current = get_draft(client, tenant)
    # Build the fake-only application first. This mode marks synthetic gate evidence;
    # the test must never instantiate a provider or need private credentials.
    settings.LLM_MODE = "openai"
    people = FilePersonaSource(settings.SEEDS_DIR).list_personas("grupo_sazon", OFFER)
    tenant_id = UUID(tenant)
    with tenant_context(tenant_id):
        passed = EvalRun.objects.create(
            tenant_id=tenant_id,
            draft_rev=current["rev"],
            persona_keys=[p.key for p in people],
            status="done",
            score_draft=100,
            max_cost_usd_micros=1000000,
            created_by=User.objects.get(email="fde@orbio.local"),
            summary={
                "harness": harness_metadata("openai"),
                **eval_snapshot(ProfileContent.model_validate(current["content"]), people),
                "offer_key": OFFER,
            },
        )
        for p in people:
            EvalCase.objects.create(
                tenant_id=tenant_id,
                run=passed,
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
    assert django_evals_gate(tenant_id, current["rev"])
    with tenant_context(tenant_id):
        EvalRun.objects.create(
            tenant_id=tenant_id,
            draft_rev=current["rev"],
            status=new_status,
            score_draft=40 if new_status == "done" else None,
            max_cost_usd_micros=1000000,
            created_by=passed.created_by,
            persona_keys=passed.persona_keys,
            summary=passed.summary,
        )
    assert not django_evals_gate(tenant_id, current["rev"])
    assert publish(client, tenant).json()["detail"] == "Faltan evals aprobadas para este borrador."


def test_gate_requires_latest_passing_evidence_for_every_open_offer(
    profile_client: tuple[Client, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant = profile_client
    draft = get_draft(client, tenant)
    draft["content"]["offers"] = [
        {"key": key, "title": key, "description": "Descripción", "status": "open"}
        for key in ("cook", "waiter")
    ]
    assert put_draft(client, tenant, draft).status_code == 200
    current = get_draft(client, tenant)
    # Gate orchestration only; run_passes has independent full-evidence tests above.
    monkeypatch.setattr(
        "adapters.persistence.evals_gate.run_passes", lambda run, profile: run.status == "done"
    )
    with tenant_context(UUID(tenant)):
        values = dict(
            tenant_id=tenant,
            draft_rev=current["rev"],
            persona_keys=[],
            status="done",
            max_cost_usd_micros=1,
            created_by=User.objects.get(email="fde@orbio.local"),
        )
        EvalRun.objects.create(**values, summary={"offer_key": "cook"})
        assert not django_evals_gate(UUID(tenant), current["rev"])
        EvalRun.objects.create(**values, summary={"offer_key": "waiter"})
        assert django_evals_gate(UUID(tenant), current["rev"])
        EvalRun.objects.create(**{**values, "status": "failed"}, summary={"offer_key": "cook"})
        assert not django_evals_gate(UUID(tenant), current["rev"])
