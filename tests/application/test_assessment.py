import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from adapters.fakes.decisions import InMemoryDecisionRepository
from adapters.fakes.evaluator import ScriptedEvaluator
from adapters.fakes.jobs import InMemoryJobQueue
from screening.application.assessment import AssessmentService, DecisionBookingPolicy
from screening.application.inbox import InboxService
from screening.application.memory import FixedProfileVersions
from screening.domain.profile import ProfileContent, ResultField
from screening.domain.result import EvaluationOutput, FieldValue
from screening.domain.run_context import RunContext
from tests.application.test_conversation_service import (
    Bundle,
)
from tests.application.test_conversation_service import (
    bundle as bundle,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def content() -> ProfileContent:
    # Decision-policy tests use a minimal legacy contract; typed extraction is tested below.
    profile = ProfileContent.model_validate_json(
        (Path(__file__).resolve().parents[1] / "fixtures/grupo_sazon_legacy_v1.json").read_text()
    )
    profile.offers = []
    profile.result_schema = [
        ResultField(key="carnet", description="Tipo de permiso", ats_field="custom.licencia")
    ]
    return profile


@dataclass
class AssessmentBundle:
    service: AssessmentService
    policy: DecisionBookingPolicy
    decisions: InMemoryDecisionRepository
    queue: InMemoryJobQueue
    evaluator: ScriptedEvaluator
    conversation: Bundle
    ctx: RunContext
    profile: ProfileContent


async def typed_profile(assessment: AssessmentBundle) -> None:
    original = await assessment.service.versions.get(
        assessment.ctx.tenant_id, assessment.conversation.original_version
    )
    draft = await assessment.service.versions.draft(assessment.ctx.tenant_id)
    assessment.service.versions = FixedProfileVersions(
        replace(original, content=assessment.profile), draft
    )


@pytest.fixture
async def assessment(bundle: Bundle) -> AssessmentBundle:
    await bundle.repo.add_message(
        bundle.tenant, bundle.session, "in", "Tengo A2", "license-evidence"
    )
    decisions, queue = InMemoryDecisionRepository(), InMemoryJobQueue()
    evaluator = ScriptedEvaluator(
        [
            EvaluationOutput(
                verdict="qualified",
                fields={"carnet": FieldValue(value="A2", evidence="Tengo A2", confidence="high")},
                flags=[],
                reason="Cumple",
                summary="Candidata apta.",
            )
        ]
    )
    service = AssessmentService(
        decisions,
        bundle.repo,
        bundle.service.versions,
        evaluator,
        queue,
        bundle.profiles.clock,
        "gpt-6-luna",
    )
    session = await bundle.repo.get_session(bundle.tenant, bundle.session)
    profile = bundle.profiles.get_version(bundle.tenant, bundle.original_version).content
    return AssessmentBundle(
        service,
        DecisionBookingPolicy(decisions, bundle.repo, queue),
        decisions,
        queue,
        evaluator,
        bundle,
        session.run_context,
        profile,
    )


async def test_qualified_applies_and_enqueues_ats_write(assessment: AssessmentBundle) -> None:
    result = await assessment.service.complete(assessment.ctx)
    assert result["next_step"] == "offer_interview"
    assert len(assessment.queue.jobs) == 1
    assert (
        await assessment.conversation.repo.get_session(
            assessment.ctx.tenant_id, assessment.ctx.session_id
        )
    ).status == "active"
    decision = next(iter(assessment.decisions.decisions.values()))
    assert decision.status == "applied" and decision.decision_type == "qualify"
    item = next(iter(assessment.decisions.outbox.values()))
    assert item.payload["fields"]["custom.licencia"] == "A2"
    assert item.payload["application_external_id"] == "lucia"


@pytest.mark.parametrize("has_license", [False, True])
async def test_typed_license_is_validated_before_decision(
    assessment: AssessmentBundle, has_license: bool
) -> None:
    assessment.profile.result_schema[0] = ResultField(
        key="carnet",
        description="Permiso vigente",
        ats_field="custom.licencia",
        value_type="boolean",
        exclude_if_false=True,
    )
    output = assessment.evaluator.outputs[0]
    assert isinstance(output, EvaluationOutput)
    quote = "Sí tengo permiso" if has_license else "No tengo permiso"
    output.fields["carnet"] = FieldValue(value=has_license, evidence=quote, confidence="high")
    await assessment.conversation.repo.add_message(
        assessment.ctx.tenant_id, assessment.conversation.session, "in", quote, "license"
    )
    await typed_profile(assessment)
    result = await assessment.service.complete(assessment.ctx)
    assert result["verdict"] == ("qualified" if has_license else "not_qualified")
    assert result["next_step"] == ("offer_interview" if has_license else "explain_review")
    assert bool(assessment.queue.jobs) is has_license


async def test_invalid_typed_result_asks_for_clarification_without_closing(
    assessment: AssessmentBundle,
) -> None:
    assessment.profile.result_schema[0].value_type = "boolean"
    await typed_profile(assessment)
    result = await assessment.service.complete(assessment.ctx)
    assert result["next_step"] == "clarify_fields"
    assert not assessment.decisions.results and not assessment.decisions.decisions
    assert not assessment.queue.jobs
    assert (
        await assessment.conversation.repo.get_session(
            assessment.ctx.tenant_id, assessment.conversation.session
        )
    ).status == "active"


@pytest.mark.parametrize(
    ("value", "evidence"),
    [("2026-02-30", "El 30 de febrero"), ("2026-11-01", "El 1 de noviembre"), ("2026-11-01", None)],
)
async def test_review_keeps_invalid_extraction_for_audit_not_as_confirmed_data(
    assessment: AssessmentBundle,
    value: str,
    evidence: str | None,
) -> None:
    assessment.profile.result_schema.append(
        ResultField(key="start_date", description="Fecha de incorporación", value_type="date")
    )
    output = assessment.evaluator.outputs[0]
    assert isinstance(output, EvaluationOutput)
    output.verdict = "review"
    output.fields["start_date"] = FieldValue(value=value, evidence=evidence, confidence="high")
    await assessment.conversation.repo.add_message(
        assessment.ctx.tenant_id,
        assessment.conversation.session,
        "in",
        "El 30 de febrero",
        "start-date",
    )
    await typed_profile(assessment)

    result = await assessment.service.complete(assessment.ctx)

    assert result["verdict"] == "review" and result["next_step"] == "explain_review"
    saved = await assessment.decisions.get_result(
        assessment.ctx.tenant_id, assessment.conversation.session
    )
    assert saved is not None
    assert saved.fields["start_date"] == {
        "value": None,
        "evidence": None,
        "confidence": "low",
    }
    assert saved.fields["carnet"]["value"] == "A2"
    assert "unclear_requirement" in saved.flags
    completed = next(
        event for event in assessment.conversation.repo.events if event[2] == "assessment_completed"
    )
    assert completed[3]["unconfirmed_fields"] == {
        "start_date": {
            "value": value,
            "evidence": evidence,
            "confidence": "high",
        }
    }
    assert not assessment.queue.jobs
    assert (
        await assessment.conversation.repo.get_session(
            assessment.ctx.tenant_id, assessment.conversation.session
        )
    ).status == "completed"
    assert await assessment.service.complete(assessment.ctx) == result
    assert (
        len([e for e in assessment.conversation.repo.events if e[2] == "assessment_completed"]) == 1
    )


async def test_review_does_not_confirm_a_wrongly_typed_free_text_answer(
    assessment: AssessmentBundle,
) -> None:
    output = assessment.evaluator.outputs[0]
    assert isinstance(output, EvaluationOutput)
    output.verdict = "review"
    output.fields["carnet"] = FieldValue(value=42, evidence="Tengo A2", confidence="high")

    await assessment.service.complete(assessment.ctx)

    saved = await assessment.decisions.get_result(
        assessment.ctx.tenant_id, assessment.conversation.session
    )
    assert saved is not None
    assert saved.fields["carnet"] == {"value": None, "evidence": None, "confidence": "low"}


@pytest.mark.parametrize("evidence", ["El 30 de febrero", None])
async def test_exclusion_keeps_other_unvalidated_fields_out_of_the_approved_ats_result(
    assessment: AssessmentBundle, evidence: str | None
) -> None:
    assessment.profile.result_schema = [
        ResultField(
            key="carnet",
            description="Permiso vigente",
            ats_field="custom.licencia",
            value_type="boolean",
            exclude_if_false=True,
        ),
        ResultField(
            key="start_date",
            description="Fecha de incorporación",
            ats_field="custom.incorporacion",
            value_type="date",
        ),
    ]
    output = assessment.evaluator.outputs[0]
    assert isinstance(output, EvaluationOutput)
    output.fields = {
        "carnet": FieldValue(value=False, evidence="No tengo permiso", confidence="high"),
        "start_date": FieldValue(
            value="2026-02-30" if evidence else "2026-11-01",
            evidence=evidence,
            confidence="high",
        ),
    }
    await assessment.conversation.repo.add_message(
        assessment.ctx.tenant_id,
        assessment.conversation.session,
        "in",
        "No tengo permiso. El 30 de febrero",
        "unmet-requirement",
    )
    await typed_profile(assessment)

    result = await assessment.service.complete(assessment.ctx)

    assert result["verdict"] == "not_qualified" and result["next_step"] == "explain_review"
    assert not assessment.queue.jobs
    inbox = InboxService(
        assessment.decisions,
        assessment.conversation.repo,
        assessment.service.versions,
        assessment.queue,
        assessment.conversation.profiles.clock,
    )
    pending = await inbox.list_pending(assessment.ctx.tenant_id)
    assert len(pending) == 1
    assert pending[0].fields["carnet"]["value"] is False
    assert pending[0].fields["start_date"] == {
        "value": None,
        "evidence": None,
        "confidence": "low",
    }
    await inbox.act(
        assessment.ctx.tenant_id,
        pending[0].decision_id,
        "confirm",
        "Requisito confirmado por selección",
        assessment.conversation.user,
    )
    item = next(iter(assessment.decisions.outbox.values()))
    assert item.payload["fields"] == {"custom.licencia": False}


async def test_qualified_with_low_dial_stays_a_proposal(assessment: AssessmentBundle) -> None:
    ctx = replace(assessment.ctx, autonomy={**assessment.ctx.autonomy, "qualify": 1})
    result = await assessment.service.complete(ctx)
    assert result["next_step"] == "explain_review"
    assert not assessment.queue.jobs
    assert next(iter(assessment.decisions.decisions.values())).status == "proposed"
    assert (
        await assessment.conversation.repo.get_session(ctx.tenant_id, ctx.session_id)
    ).status == "completed"


async def test_injection_flag_requires_human_even_at_maximum_dial(
    assessment: AssessmentBundle,
) -> None:
    assessment.evaluator.outputs[0].flags = ["prompt_injection_attempt"]
    ctx = replace(assessment.ctx, autonomy={**assessment.ctx.autonomy, "qualify": 3})
    assert (await assessment.service.complete(ctx))["next_step"] == "explain_review"
    assert next(iter(assessment.decisions.decisions.values())).status == "proposed"
    assert not assessment.queue.jobs


async def test_reject_is_a_proposal_and_closes_kindly(assessment: AssessmentBundle) -> None:
    assessment.evaluator.outputs[0].verdict = "not_qualified"
    result = await assessment.service.complete(assessment.ctx)
    assert result["next_step"] == "explain_review"
    decision = next(iter(assessment.decisions.decisions.values()))
    assert decision.status == "proposed" and decision.decision_type == "reject"
    assert not assessment.queue.jobs


async def test_evaluation_failure_becomes_explicit_review(assessment: AssessmentBundle) -> None:
    assessment.evaluator.outputs = [RuntimeError("failed")]
    result = await assessment.service.complete(assessment.ctx)
    assert result["verdict"] == "review"
    saved = await assessment.decisions.get_result(
        assessment.ctx.tenant_id, assessment.ctx.session_id
    )
    assert saved is not None and saved.flags == ["evaluation_failed"]
    assert all(field["value"] is None for field in saved.fields.values())


async def test_handoff_cannot_be_overwritten_by_assessment(assessment: AssessmentBundle) -> None:
    await assessment.conversation.repo.set_session_status(
        assessment.ctx.tenant_id, assessment.ctx.session_id, "handed_off"
    )
    assert (await assessment.service.complete(assessment.ctx))["next_step"] == "explain_review"
    assert not assessment.evaluator.requests
    assert not assessment.decisions.decisions
    assert (
        await assessment.conversation.repo.get_session(
            assessment.ctx.tenant_id, assessment.ctx.session_id
        )
    ).status == "handed_off"


async def test_legacy_high_reject_dial_still_requires_human(assessment: AssessmentBundle) -> None:
    assessment.evaluator.outputs[0].verdict = "not_qualified"
    ctx = replace(assessment.ctx, autonomy={**assessment.ctx.autonomy, "reject": 2})
    assert (await assessment.service.complete(ctx))["next_step"] == "explain_review"
    assert not assessment.queue.jobs
    assert next(iter(assessment.decisions.decisions.values())).status == "proposed"


async def test_completion_is_idempotent_even_when_called_concurrently(
    assessment: AssessmentBundle,
) -> None:
    first, second = await asyncio.gather(
        assessment.service.complete(assessment.ctx), assessment.service.complete(assessment.ctx)
    )
    assert first == second
    assert len(assessment.evaluator.requests) == 1
    assert len(assessment.decisions.results) == len(assessment.decisions.decisions) == 1
    assert len(assessment.queue.jobs) == 1


async def test_booking_policy_needs_applied_qualify_and_closes_session(
    assessment: AssessmentBundle,
) -> None:
    assert not await assessment.policy.can_book(assessment.ctx)
    await assessment.service.complete(assessment.ctx)
    assert await assessment.policy.can_book(assessment.ctx)
    await assessment.policy.booked(
        assessment.ctx,
        assessment.profile,
        {
            "starts_at_local": "lunes 26/10/2026 10:00",
            "site_name": "Sazón Benimaclet",
        },
    )
    assert not await assessment.policy.can_book(assessment.ctx)
    assert len(assessment.queue.jobs) == 2
    stage = next(
        item for item in assessment.decisions.outbox.values() if item.kind == "ats.set_stage"
    )
    assert stage.payload["stage"] == "Entrevista"
    assert (
        await assessment.conversation.repo.get_session(
            assessment.ctx.tenant_id, assessment.ctx.session_id
        )
    ).status == "completed"
