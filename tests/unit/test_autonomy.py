import pytest

from screening.domain.autonomy import DecisionType, decide_outcome, next_step


@pytest.mark.parametrize("kind", ["qualify", "reject", "review", "pool"])
@pytest.mark.parametrize("level", range(4))
@pytest.mark.parametrize("flags", [[], ["prompt_injection_attempt"]])
def test_outcome_table(kind: DecisionType, level: int, flags: list[str]) -> None:
    expected = "apply" if kind == "qualify" and level >= 2 and not flags else "propose"
    assert decide_outcome(kind, level, flags) == expected


@pytest.mark.parametrize(
    "verdict,applied,expected",
    [
        ("qualified", True, "offer_interview"),
        ("qualified", False, "explain_review"),
        ("not_qualified", True, "close_kindly"),
        ("not_qualified", False, "explain_review"),
        ("review", True, "explain_review"),
        ("review", False, "explain_review"),
        ("pool", True, "offer_pool"),
        ("pool", False, "offer_pool"),
    ],
)
def test_next_step_table(verdict: str, applied: bool, expected: str) -> None:
    assert next_step(verdict, applied) == expected
