from pathlib import Path

import pytest
from pydantic import ValidationError

from screening.domain.fit import assess_fit
from screening.domain.profile import (
    FitCriterion,
    OfferDefinition,
    ProfileContent,
    profile_for_offer,
)


def profile() -> ProfileContent:
    return ProfileContent.model_validate_json(
        Path("seeds/tenants/grupo_sazon/profile.json").read_text()
    )


def test_offer_is_a_specific_brief_not_a_customer_fork() -> None:
    base = profile()
    base.offers = [
        OfferDefinition(
            key="cocina",
            title="Cocinero",
            description="Dos años en cocina",
            instructions="Pregunta por turnos",
            capabilities=["handoff.request"],
        )
    ]
    base.brief.who = ""
    resolved = profile_for_offer(base, "cocina")
    assert resolved.brief.who == "Dos años en cocina"
    assert resolved.brief.role.startswith(base.brief.role)
    assert "Oferta: Cocinero\nDos años en cocina" in resolved.brief.role
    assert resolved.brief.fit == base.brief.fit
    assert "Pregunta por turnos" in resolved.operating_model
    assert resolved.grants.conversador == ["handoff.request"]
    assert base.brief.role != resolved.brief.role
    assert base.connections == resolved.connections


def test_offer_cannot_invent_fields_or_expand_grants() -> None:
    base = profile().model_dump()
    base["offers"] = [
        {"key": "a", "title": "A", "criteria": [{"key": "x", "label": "X", "field": "absent"}]}
    ]
    with pytest.raises(ValidationError, match="campo de la oferta"):
        ProfileContent.model_validate(base)
    base["offers"] = [{"key": "a", "title": "A", "capabilities": ["analytics.aggregates"]}]
    with pytest.raises(ValidationError, match="ampliar"):
        ProfileContent.model_validate(base)


def test_fit_separates_missing_and_required_from_score() -> None:
    offer = OfferDefinition(
        key="cook",
        title="Cocinero",
        criteria=[
            FitCriterion(
                key="experience",
                label="Experiencia",
                field="years",
                operator="minimum",
                expected=2,
                weight=2,
            ),
            FitCriterion(
                key="shift",
                label="Disponibilidad",
                field="available",
                operator="equals",
                expected=True,
                required=True,
            ),
        ],
    )
    fields = {"years": {"value": 3, "evidence": "Tres años", "confidence": "high"}}
    partial = assess_fit(offer, fields)
    assert partial["score"] is None and partial["coverage"] == 50
    assert partial["requirements"] == "pending"
    fields["available"] = {"value": False, "evidence": "No puedo", "confidence": "high"}
    result = assess_fit(offer, fields)
    assert result["score"] == 67 and result["requirements"] == "not_met"
    fields["years"]["confidence"] = "low"
    assert assess_fit(offer, fields)["score"] is None


def test_criterion_types_and_numeric_equality() -> None:
    base = profile().model_dump()
    base["offers"] = [
        {
            "key": "cook",
            "title": "Cocinero",
            "fields": [{"key": "years", "description": "Años", "value_type": "number"}],
            "criteria": [
                {
                    "key": "experience",
                    "label": "Experiencia",
                    "field": "years",
                    "operator": "equals",
                    "expected": True,
                }
            ],
        }
    ]
    with pytest.raises(ValidationError, match="mismo tipo"):
        ProfileContent.model_validate(base)
    base["offers"][0]["criteria"][0]["expected"] = 2
    valid = ProfileContent.model_validate(base)
    assert (
        assess_fit(
            valid.offers[0], {"years": {"value": 2, "evidence": "Dos", "confidence": "high"}}
        )["score"]
        == 100
    )


def test_offer_personas_are_isolated_with_legacy_fallback(tmp_path: Path) -> None:
    from adapters.personas import FilePersonaSource

    original = Path("seeds/tenants/grupo_sazon/personas.json").read_text()
    root = tmp_path / "tenants" / "client"
    specific = root / "offers" / "cook"
    specific.mkdir(parents=True)
    (root / "personas.json").write_text(original)
    (specific / "personas.json").write_text(original.replace("Lucía", "Clara"))
    source = FilePersonaSource(tmp_path)
    assert source.list_personas("client", "unknown") == source.list_personas("client")
    assert source.list_personas("client", "cook") != source.list_personas("client")
    with pytest.raises(ValueError):
        source.list_personas("client", "../../escape")


def test_explicit_offer_context_and_empty_custom_fields_do_not_inherit() -> None:
    data = profile().model_dump()
    data.update(schema_version=3)
    data["brief"].update(role="Empresa de hostelería", who="", fit="")
    data["offers"] = [
        {
            "key": "cook",
            "title": "Cocinero",
            "description": "Trabajo en cocina",
            "fields_mode": "custom",
            "fields": [],
            "source_ids": [],
            "capabilities": ["handoff.request"],
        }
    ]
    base = ProfileContent.model_validate(data)
    effective = profile_for_offer(base, "cook")
    assert effective.result_schema == []
    assert effective.sources == []
    assert "Trabajo en cocina" in effective.brief.role
    assert effective.brief.who == effective.brief.fit == ""
    assert ProfileContent.model_validate(effective.model_dump()) == effective
    assert profile_for_offer(effective, "cook") == effective
    assert base.result_schema and base.sources
    data["offers"][0]["fields_mode"] = "inherit"
    inherited = profile_for_offer(ProfileContent.model_validate(data), "cook")
    assert inherited.result_schema == base.result_schema


def test_explicit_configuration_rejects_ambiguous_fields_and_unknown_sources() -> None:
    data = profile().model_dump()
    data["offers"] = [{"key": "cook", "title": "Cocinero", "source_ids": ["unknown"]}]
    with pytest.raises(ValidationError, match="fuente"):
        ProfileContent.model_validate(data)
    data["offers"][0].update(
        source_ids=[],
        fields_mode="inherit",
        fields=[
            {"key": "years", "description": "Años"},
        ],
    )
    with pytest.raises(ValidationError, match="heredar"):
        ProfileContent.model_validate(data)


def test_legacy_offer_view_is_serializable_with_disjoint_sources() -> None:
    data = profile().model_dump()
    data["offers"] = [
        {"key": "a", "title": "A", "source_ids": [data["sources"][0]["id"]]},
        {"key": "b", "title": "B", "source_ids": [data["sources"][1]["id"]]},
    ]
    base = ProfileContent.model_validate(data)
    effective = profile_for_offer(base, "a")
    assert ProfileContent.model_validate(effective.model_dump()) == effective
    assert len(effective.sources) == 1
    assert len(base.offers) == 2
