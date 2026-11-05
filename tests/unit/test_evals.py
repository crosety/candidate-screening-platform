import json
from pathlib import Path

from screening.domain.evals import (
    JudgeCheck,
    JudgeItem,
    JudgeOutput,
    Persona,
    case_score,
    normalize_judge,
)
from screening.domain.profile import RubricItem


def test_weighted_score_and_verdict_cap() -> None:
    rubric = [
        RubricItem(key="a", description="A", weight=1),
        RubricItem(key="b", description="B", weight=3),
    ]
    judge = JudgeOutput(
        items=[JudgeItem(key="a", score=0, comment=""), JudgeItem(key="b", score=100, comment="")],
        checks=[],
        comment="",
    )
    assert case_score(judge, rubric, "qualified", "qualified") == 85
    assert case_score(judge, [], "qualified", "qualified") == 100
    assert case_score(judge, rubric, "review", "qualified") == 40


def test_normalization_fills_missing_and_accepts_prefixed_checks() -> None:
    persona = Persona(
        key="test",
        name="Test",
        facts="",
        style="",
        expected_verdict="qualified",
        must=["Preguntar"],
        must_not=["Prometer"],
    )
    judge = JudgeOutput(
        items=[JudgeItem(key="unknown", score=100, comment="")],
        checks=[
            JudgeCheck(text="Debe: Preguntar", passed=True, evidence="sí"),
            JudgeCheck(text="No debe: Prometer", passed=True, evidence="no"),
        ],
        comment="",
    )
    result = normalize_judge(judge, [RubricItem(key="a", description="A", weight=1)], persona)
    assert result.items[0].score == 0 and len(result.items) == 1
    assert all(item.passed for item in result.checks)
    assert case_score(result, [], "qualified", "qualified") == 100
    missing = normalize_judge(JudgeOutput(items=[], checks=[], comment=""), [], persona)
    assert not any(item.passed for item in missing.checks)


def test_seed_personas_validate() -> None:
    for path in (Path(__file__).resolve().parents[2] / "seeds/tenants").glob("*/personas.json"):
        for item in json.loads(path.read_text()):
            Persona.model_validate(item)
