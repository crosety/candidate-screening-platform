from uuid import UUID

from django.conf import settings

from adapters.eval_harness import harness_metadata
from adapters.personas import FilePersonaSource
from screening.domain.eval_identity import eval_snapshot
from screening.domain.profile import ProfileContent
from web.evals.models import EvalCase, EvalRun
from web.profiles.models import ImprovementProposal, ProfileDraft, Publication
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def run_passes(run: EvalRun, profile: ProfileContent) -> bool:
    """One fail-closed validator for publication, UI and proposal acceptance."""
    active = (
        Publication.objects.filter(tenant_id=run.tenant_id, state="active", segment="")
        .select_related("version")
        .first()
    )
    threshold = max(
        profile.eval_threshold,
        float(active.version.content.get("eval_threshold", 80)) if active else 0,
    )
    slug = Tenant.objects.get(pk=run.tenant_id).slug
    people = FilePersonaSource(settings.SEEDS_DIR).list_personas(
        slug, str(run.summary.get("offer_key", ""))
    )
    harness = harness_metadata(settings.LLM_MODE)
    if (
        not people
        or harness["mode"] not in ("openai", "bedrock")
        or run.status != "done"
        or run.score_draft is None
        or run.score_draft < threshold
        or run.summary.get("errors")
        or run.summary.get("harness") != harness
        or any(run.summary.get(k) != v for k, v in eval_snapshot(profile, people).items())
    ):
        return False
    expected = {p.key: p for p in people}
    cases = list(EvalCase.objects.filter(tenant_id=run.tenant_id, run=run, target="draft"))
    if len(cases) != len(expected) or {c.persona_key for c in cases} != set(expected):
        return False
    for case in cases:
        persona = expected[case.persona_key]
        checks = case.judge.get("checks", [])
        expected_checks = {"Debe: " + t for t in persona.must} | {
            "No debe: " + t for t in persona.must_not
        }
        if (
            case.status != "done"
            or case.judge.get("verdict") != persona.expected_verdict
            or case.score is None
            or case.score < threshold
            or {x.get("text") for x in checks} != expected_checks
            or any(x.get("passed") is not True for x in checks)
        ):
            return False
    return True


def passing_eval_run(tenant_id: UUID, rev: int) -> EvalRun | None:
    with tenant_context(tenant_id):
        draft = ProfileDraft.objects.get(tenant_id=tenant_id)
        if draft.rev != rev:
            return None
        profile = ProfileContent.model_validate(draft.content)
        accepted = set(
            ImprovementProposal.objects.filter(
                tenant_id=tenant_id, state="accepted", accepted_rev=rev, content=draft.content
            ).values_list("eval_run_id", flat=True)
        )
        required = {o.key for o in profile.offers if o.status == "open"} or {""}
        checked: set[str] = set()
        passing: EvalRun | None = None
        for run in EvalRun.objects.filter(tenant_id=tenant_id).order_by("-created_at", "-id"):
            ordinary = run.draft_rev == rev and run.alternative_content is None
            alternative = run.id in accepted and run.alternative_content == draft.content
            if ordinary or alternative:
                # New evidence supersedes a previous pass, including pending or failed runs.
                # Never cherry-pick an older success after observing a regression.
                key = str(run.summary.get("offer_key", ""))
                if key not in required or key in checked:
                    continue
                checked.add(key)
                if not run_passes(run, profile):
                    return None
                passing = run
                if checked == required:
                    return passing
        return None


def django_evals_gate(tenant_id: UUID, rev: int) -> bool:
    return passing_eval_run(tenant_id, rev) is not None
