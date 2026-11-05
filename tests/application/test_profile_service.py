import random
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from adapters.fakes.clock import FixedClock
from adapters.fakes.profiles import InMemoryProfileRepository
from screening.application.profiles import ProfileService
from screening.domain.errors import NotAllowed, NotFound
from screening.domain.profile import ProfileContent, ResultField


@pytest.fixture
def bundle() -> tuple[ProfileService, InMemoryProfileRepository, UUID, UUID]:
    path = Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon/profile.json"
    content = ProfileContent.model_validate_json(path.read_text(encoding="utf-8"))
    tenant, user = uuid4(), uuid4()
    repo = InMemoryProfileRepository()
    repo.add_tenant(tenant, "Client")
    version = repo.create_version(tenant, content, "medium", "Initial", user)
    publication = repo.create_publication(tenant, version.id, None, 100, False, False, "", user)
    repo.activate(tenant, publication.id)
    repo.replace_draft(tenant, content, version.id, user)
    service = ProfileService(repo, FixedClock(datetime(2026, 10, 22, tzinfo=UTC)))
    return service, repo, tenant, user


def changed(service: ProfileService, tenant: UUID, user: UUID) -> None:
    draft = service.get_draft(tenant)
    draft.content.brief.fit += " Nueva frase."
    service.save_draft(tenant, draft.rev, draft.content, user)


def test_publish_without_changes(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, _, tenant, user = bundle
    with pytest.raises(NotAllowed, match="No hay cambios"):
        service.publish(tenant, "", 100, user)


def test_publish_with_errors(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, _, tenant, user = bundle
    draft = service.get_draft(tenant)
    draft.content.result_schema[0].ats_field = ""
    service.save_draft(tenant, draft.rev, draft.content, user)
    with pytest.raises(NotAllowed, match="hay errores"):
        service.publish(tenant, "", 100, user)


def test_evals_gate(bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID]) -> None:
    service, repo, tenant, user = bundle
    changed(service, tenant, user)
    gated = ProfileService(repo, service.clock, lambda _tenant, _rev: False)
    with pytest.raises(NotAllowed, match="Faltan evals"):
        gated.publish(tenant, "", 100, user)


def test_live_warnings_are_w005(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, repo, tenant, _ = bundle
    live = ProfileService(repo, service.clock, live_warnings=lambda _: ["Local sin agenda"])
    assert any(
        check.code == "W005" and check.message == "Local sin agenda"
        for check in live.checks(tenant)
    )


@pytest.mark.parametrize("percent", [0, 101])
def test_percent_bounds(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID], percent: int
) -> None:
    service, _, tenant, user = bundle
    changed(service, tenant, user)
    with pytest.raises(NotAllowed, match="porcentaje"):
        service.publish(tenant, "", percent, user)


def test_medium_change_activates_and_resets_draft(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, repo, tenant, user = bundle
    changed(service, tenant, user)
    publication = service.publish(tenant, "Brief", 100, user)
    assert publication.state == "active"
    assert repo.get_version(tenant, publication.version_id).version == "1.1"
    assert service.diff(tenant).risk == "none"
    assert service.get_draft(tenant).base_version_id == publication.version_id


def test_high_change_needs_another_fde(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, _, tenant, user = bundle
    draft = service.get_draft(tenant)
    draft.content.autonomy.qualify = 3
    service.save_draft(tenant, draft.rev, draft.content, user)
    publication = service.publish(tenant, "Dial", 100, user)
    assert publication.state == "pending_approval"
    with pytest.raises(NotAllowed, match="otra persona"):
        service.approve(tenant, publication.id, "second_fde", user)
    assert service.approve(tenant, publication.id, "second_fde", uuid4()).state == "active"


def test_client_approval_and_second_fde(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, _, tenant, user = bundle
    draft = service.get_draft(tenant)
    draft.content.result_schema.append(
        ResultField(key="extra", description="Dato", ats_field="custom.x")
    )
    service.save_draft(tenant, draft.rev, draft.content, user)
    publication = service.publish(tenant, "Contrato", 100, user)
    assert publication.needs_client_approval and publication.needs_second_approval
    assert service.approve(tenant, publication.id, "client", user).state == "pending_approval"
    assert service.approve(tenant, publication.id, "second_fde", uuid4()).state == "active"


def test_unnecessary_approval(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, _, tenant, user = bundle
    changed(service, tenant, user)
    publication = service.publish(tenant, "", 100, user)
    with pytest.raises(NotAllowed, match="no hace falta"):
        service.approve(tenant, publication.id, "client", user)


def test_pilot_distribution(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, repo, tenant, user = bundle
    old = service.version_for_new_session(tenant, uuid4())
    changed(service, tenant, user)
    publication = service.publish(tenant, "Pilot", 50, user)
    rng = random.Random(42)
    selected = [
        service.version_for_new_session(tenant, UUID(int=rng.getrandbits(128))).id
        for _ in range(1000)
    ]
    assert 400 <= selected.count(publication.version_id) <= 600
    assert set(selected) == {old.id, publication.version_id}
    assert repo.get_publication(tenant, publication.id).percent == 50


def test_rollback_creates_new_version(
    bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID],
) -> None:
    service, repo, tenant, user = bundle
    original = service.version_for_new_session(tenant, uuid4())
    changed(service, tenant, user)
    service.publish(tenant, "Brief", 100, user)
    restored = service.rollback(tenant, original.id, "Restore", user)
    version = repo.get_version(tenant, restored.version_id)
    assert version.version == "1.2"
    assert version.content == original.content
    assert restored.state == "active"


def test_no_active_version() -> None:
    service = ProfileService(
        InMemoryProfileRepository(), FixedClock(datetime(2026, 10, 22, tzinfo=UTC))
    )
    with pytest.raises(NotFound, match="ninguna versión"):
        service.version_for_new_session(uuid4(), uuid4())


def test_reset_draft(bundle: tuple[ProfileService, InMemoryProfileRepository, UUID, UUID]) -> None:
    service, _, tenant, user = bundle
    original = service.get_draft(tenant).content
    changed(service, tenant, user)
    assert service.reset_draft(tenant, user).content == original


def test_rollback_cannot_bypass_pending_approvals(bundle):
    service, repo, tenant, user = bundle
    draft = service.get_draft(tenant)
    draft.content.result_schema.append(ResultField(key="extra", description="Dato", ats_field="x"))
    service.save_draft(tenant, draft.rev, draft.content, user)
    pending = service.publish(tenant, "Pending", 100, user)
    with pytest.raises(NotAllowed, match="haya estado activa"):
        service.rollback(tenant, pending.version_id, "Bypass", user)
    assert repo.get_publication(tenant, pending.id).state == "pending_approval"


def test_new_activation_cancels_pending_and_preserves_new_draft(bundle):
    service, repo, tenant, user = bundle
    draft = service.get_draft(tenant)
    draft.content.result_schema.append(ResultField(key="extra", description="Dato", ats_field="x"))
    service.save_draft(tenant, draft.rev, draft.content, user)
    pending = service.publish(tenant, "Pending", 100, user)
    service.reset_draft(tenant, user)
    changed(service, tenant, user)
    active = service.publish(tenant, "New", 100, user)
    changed(service, tenant, user)
    latest = service.get_draft(tenant)
    with pytest.raises(NotAllowed, match="ya no está pendiente"):
        service.approve(tenant, pending.id, "second_fde", uuid4())
    assert repo.active_publication(tenant).id == active.id
    assert service.get_draft(tenant) == latest


def test_approval_preserves_edits_made_after_submission(bundle):
    service, _, tenant, user = bundle
    draft = service.get_draft(tenant)
    draft.content.result_schema.append(ResultField(key="extra", description="Dato", ats_field="x"))
    service.save_draft(tenant, draft.rev, draft.content, user)
    pending = service.publish(tenant, "Pending", 100, user)
    changed(service, tenant, user)
    latest = service.get_draft(tenant).content
    service.approve(tenant, pending.id, "client", user)
    service.approve(tenant, pending.id, "second_fde", uuid4())
    assert service.get_draft(tenant).content == latest
