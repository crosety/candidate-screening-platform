from pathlib import Path
from uuid import UUID, uuid4

import pytest

from adapters.fakes.profiles import InMemoryProfileRepository
from adapters.persistence.profiles import DjangoProfileRepository
from screening.application.ports import ProfileRepository
from screening.domain.errors import Conflict, NotFound
from screening.domain.profile import ProfileContent
from web.accounts.models import User
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def content() -> ProfileContent:
    path = Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon/profile.json"
    return ProfileContent.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.fixture(params=["memory", "django"])
def repository(request: pytest.FixtureRequest) -> tuple[ProfileRepository, UUID, UUID]:
    tenant = Tenant.objects.create(slug="contract", name="Contract")
    user = User.objects.create_user("fde@example.test", "test-only", full_name="FDE")
    if request.param == "memory":
        memory = InMemoryProfileRepository()
        memory.add_tenant(tenant.id, tenant.name)
        repo: ProfileRepository = memory
    else:
        repo = DjangoProfileRepository()
    return repo, tenant.id, user.id


def test_rejects_stale_revision(
    repository: tuple[ProfileRepository, UUID, UUID], content: ProfileContent
) -> None:
    repo, tenant, user = repository
    draft = repo.replace_draft(tenant, content, None, user)
    saved = repo.save_draft(tenant, draft.rev, content, user)
    assert saved.rev == draft.rev + 1
    with pytest.raises(Conflict):
        repo.save_draft(tenant, draft.rev, content, user)


def test_version_numbers(
    repository: tuple[ProfileRepository, UUID, UUID], content: ProfileContent
) -> None:
    repo, tenant, user = repository
    versions = [repo.create_version(tenant, content, "medium", "", user) for _ in range(3)]
    assert [v.version for v in versions] == ["1.0", "1.1", "1.2"]
    assert len({v.content_hash for v in versions}) == 1
    assert len(versions[0].content_hash) == 64


def test_one_active_publication(
    repository: tuple[ProfileRepository, UUID, UUID], content: ProfileContent
) -> None:
    repo, tenant, user = repository
    a = repo.create_version(tenant, content, "medium", "", user)
    pa = repo.create_publication(tenant, a.id, None, 100, False, False, "", user)
    repo.activate(tenant, pa.id)
    b = repo.create_version(tenant, content, "medium", "", user)
    pb = repo.create_publication(tenant, b.id, a.id, 100, False, False, "", user)
    repo.activate(tenant, pb.id)
    active = repo.active_publication(tenant)
    assert active is not None and active.id == pb.id
    assert repo.get_publication(tenant, pa.id).state == "superseded"


def test_other_tenant_version_is_not_found(
    repository: tuple[ProfileRepository, UUID, UUID], content: ProfileContent
) -> None:
    repo, tenant, user = repository
    version = repo.create_version(tenant, content, "medium", "", user)
    with pytest.raises(NotFound):
        repo.get_version(uuid4(), version.id)
