from pathlib import Path

from screening.domain.offer_configuration import ConfigurationPlan, prepare_configuration
from screening.domain.profile import ProfileContent, profile_for_offer


def test_preparation_is_explicit_preserves_history_and_is_idempotent():
    old = ProfileContent.model_validate_json(
        Path("seeds/tenants/grupo_sazon/profile.json").read_text()
    )
    before = old.model_dump()
    plan = ConfigurationPlan(
        company_description="Restauración",
        company_instructions="Políticas comunes",
        offers={
            "repartidores": {"source_ids": ["manual_repartidor"], "move_legacy_requirements": True}
        },
    )
    new = prepare_configuration(old, plan)
    assert new.schema_version == 3 and not new.brief.who and not new.brief.fit
    assert new.result_schema == []
    offer = profile_for_offer(new, "repartidores")
    assert offer.result_schema == old.result_schema
    assert old.brief.who in offer.brief.role
    assert [s.id for s in offer.sources] == ["manual_repartidor"]
    assert old.model_dump() == before
    assert prepare_configuration(new, plan) == new
