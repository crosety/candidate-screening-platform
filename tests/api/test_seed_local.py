from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from adapters.persistence.profiles import DjangoProfileRepository
from web.accounts.models import User
from web.conversations.models import CandidateLink
from web.profiles.models import ProfileDraft, ProfileVersion
from web.simulated.models import AtsRecord, CalendarSlot, Place, Site
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership, Tenant

pytestmark = pytest.mark.django_db


def test_seed_is_idempotent(settings: Any) -> None:
    settings.SEED_PASSWORD = "test-only-password"
    call_command("seed_local")
    first_counts = {}
    for tenant in Tenant.objects.all():
        with tenant_context(tenant.id):
            first_counts[tenant.id] = (
                Site.objects.count(),
                Place.objects.count(),
                AtsRecord.objects.count(),
                CalendarSlot.objects.count(),
                CandidateLink.objects.filter(tenant=tenant).count(),
            )
    call_command("seed_local")
    assert Tenant.objects.count() == 2
    assert User.objects.count() == 3
    assert Membership.objects.count() == 4
    assert User.objects.get(email="fde@orbio.local").check_password("test-only-password")
    for tenant in Tenant.objects.all():
        with tenant_context(tenant.id):
            assert ProfileVersion.objects.count() == 1
            assert ProfileDraft.objects.count() == 1
            assert (
                Site.objects.count(),
                Place.objects.count(),
                AtsRecord.objects.count(),
                CalendarSlot.objects.count(),
                CandidateLink.objects.filter(tenant=tenant).count(),
            ) == first_counts[tenant.id]
            assert first_counts[tenant.id][0] > 0
            assert first_counts[tenant.id][2] > 0


def test_existing_profiles_are_preserved(settings: Any) -> None:
    settings.SEED_PASSWORD = "test-only-password"
    call_command("seed_local")
    tenant = Tenant.objects.get(slug="grupo_sazon")
    user = User.objects.get(email="fde@orbio.local")
    repo = DjangoProfileRepository()
    draft = repo.get_draft(tenant.id)
    draft.content.brief.fit += " Un cambio del FDE."
    saved = repo.save_draft(tenant.id, draft.rev, draft.content, user.id)
    call_command("seed_local")
    assert repo.get_draft(tenant.id) == saved


def test_password_is_required(settings: Any) -> None:
    settings.SEED_PASSWORD = ""
    with pytest.raises(CommandError, match=r"Falta SEED_PASSWORD en \.env"):
        call_command("seed_local")
    assert User.objects.count() == 0
