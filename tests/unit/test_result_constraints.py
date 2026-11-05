from datetime import date

import pytest

from screening.domain.profile import ResultField
from screening.domain.result import FieldValue, invalid_field, quoted_in


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("boolean", True, False),
        ("boolean", False, False),
        ("boolean", "sí", True),
        ("boolean", None, True),
        ("number", 0, False),
        ("number", -1, True),
        ("number", True, True),
        ("number", "dos", True),
        ("number", float("inf"), True),
        ("enum", "flexible", False),
        ("enum", "cualquiera", True),
        ("date", "2026-10-24", False),
        ("date", "2026-10-23", False),
        ("date", "2026-10-22", True),
        ("date", "mañana", True),
    ],
)
def test_typed_result_validation(kind: str, value: object, expected: bool) -> None:
    field = ResultField.model_validate(
        {
            "key": "field",
            "description": "Prueba",
            "value_type": kind,
            "choices": ["mañana", "tarde", "noche", "flexible"],
            "minimum": 0,
            "future_date": kind == "date",
        }
    )
    item = FieldValue.model_validate({"value": value, "evidence": "cita", "confidence": "high"})
    assert invalid_field(field, item, date(2026, 10, 23)) is expected


def test_optional_unknown_is_not_zero_or_false() -> None:
    field = ResultField(key="years", description="Años", required=False, value_type="number")
    assert not invalid_field(
        field, FieldValue(value=None, evidence=None, confidence="low"), date.today()
    )


CANDIDATE = ["El lunes que viene.", "Sí, todo correcto.", "No, no he repartido por otros medios."]


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("El lunes que viene.", True),
        ("el lunes que viene", True),  # case and edge punctuation are not evidence
        ("«Sí, todo correcto.»", True),
        ("El lunes que viene. / Sí, todo correcto.", True),  # two literal quotes
        ("Sí, todo correcto. [...] No, no he repartido", True),
        ("El lunes que viene. Confirmó el resumen con: «Sí, todo correcto.»", False),
        ("El lunes que viene / lunes 2 de noviembre", False),  # agent's words, not the candidate's
        ("El lunes que viene / s", False),  # a one-letter fragment proves nothing
        ("", False),
        (None, False),
    ],
)
def test_evidence_must_be_the_candidates_literal_words(
    evidence: str | None, expected: bool
) -> None:
    assert quoted_in(evidence, CANDIDATE) is expected
