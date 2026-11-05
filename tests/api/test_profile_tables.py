import hashlib
import json
from pathlib import Path

import pytest
from django.db import DatabaseError

from adapters.persistence.profiles import DjangoProfileRepository
from web.accounts.models import User
from web.profiles.models import ProfileVersion
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


def test_profile_versions_are_append_only() -> None:
    tenant = Tenant.objects.create(slug="example", name="Example")
    user = User.objects.create_user("fde@example.test", "test-only", full_name="FDE")
    with tenant_context(tenant.id):
        version = ProfileVersion.objects.create(
            tenant=tenant,
            seq=1,
            version="1.0",
            content={},
            content_hash="0" * 64,
            schema_version=1,
            risk="medium",
            published_by=user,
        )
    with pytest.raises(DatabaseError), tenant_context(tenant.id):
        ProfileVersion.objects.filter(pk=version.id).update(note="changed")


def test_reading_legacy_profile_does_not_rewrite_published_content() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures/grupo_sazon_legacy_v1.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    assert legacy["schema_version"] == 1
    canonical = json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    tenant = Tenant.objects.create(slug="legacy", name="Legacy")
    user = User.objects.create_user("legacy@example.test", "test-only", full_name="FDE")
    with tenant_context(tenant.id):
        version = ProfileVersion.objects.create(
            tenant=tenant,
            seq=1,
            version="1.0",
            content=legacy,
            content_hash=content_hash,
            schema_version=1,
            risk="medium",
            published_by=user,
        )
    loaded = DjangoProfileRepository().get_version(tenant.id, version.id)
    assert loaded.content.schema_version == 2
    assert loaded.content.channel_settings.model_dump(exclude_none=True) == {}
    assert loaded.content_hash == content_hash
    with tenant_context(tenant.id):
        version.refresh_from_db()
        assert version.content == legacy
        assert version.schema_version == 1
        assert version.content_hash == content_hash
