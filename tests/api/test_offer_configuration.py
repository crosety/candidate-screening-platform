import json
import stat

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.profiles.models import ProfileVersion
from web.tenancy.context import tenant_context

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def test_preparing_configuration_is_draft_only_private_and_revision_guarded(phone, tmp_path):
    _, tenant, _ = phone
    service = get_container().profiles
    original = service.get_draft(tenant)
    live = service.active_version(tenant)
    assert live
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "company_description": "Empresa de restauración",
                "company_instructions": "Políticas comunes",
                "offers": {
                    o.key: {"source_ids": [], "move_legacy_requirements": True}
                    for o in original.content.offers
                },
            }
        )
    )
    options = {"tenant": "grupo_sazon", "actor": "fde@orbio.local", "expected_rev": original.rev}
    call_command("prepare_offer_configuration", str(plan), **options)
    assert service.get_draft(tenant).rev == original.rev
    with pytest.raises(CommandError, match="backup"):
        call_command("prepare_offer_configuration", str(plan), apply=True, **options)
    backup = tmp_path / "before.json"
    call_command("prepare_offer_configuration", str(plan), apply=True, backup=backup, **options)
    changed = service.get_draft(tenant)
    assert changed.rev == original.rev + 1
    assert changed.content.schema_version == 3
    assert [(o.key, o.status) for o in changed.content.offers] == [
        (o.key, o.status) for o in original.content.offers
    ]
    assert json.loads(backup.read_text())["content"] == original.content.model_dump(mode="json")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert service.active_version(tenant) == live
    with tenant_context(tenant):
        assert ProfileVersion.objects.get(pk=live.id).content == live.content.model_dump(
            mode="json"
        )
    with pytest.raises(CommandError, match="cambiado"):
        call_command("prepare_offer_configuration", str(plan), apply=True, backup=backup, **options)
    call_command(
        "prepare_offer_configuration",
        str(plan),
        apply=True,
        backup=backup,
        **{**options, "expected_rev": changed.rev},
    )
    assert service.get_draft(tenant).rev == changed.rev
