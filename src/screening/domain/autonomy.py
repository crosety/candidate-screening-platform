from typing import Literal

DecisionType = Literal["qualify", "reject", "review", "pool"]
NextStep = Literal["offer_interview", "close_kindly", "explain_review", "offer_pool"]

DECISION_FOR_VERDICT: dict[str, DecisionType] = {
    "qualified": "qualify",
    "not_qualified": "reject",
    "review": "review",
    "pool": "pool",
}


def decide_outcome(
    decision_type: DecisionType,
    level: int,
    flags: list[str],
) -> Literal["apply", "propose"]:
    # Legacy profiles may contain reject=2; runtime still requires human review.
    if decision_type == "qualify" and level >= 2 and not flags:
        return "apply"
    return "propose"


def next_step(verdict: str, applied: bool) -> NextStep:
    if verdict == "qualified":
        return "offer_interview" if applied else "explain_review"
    if verdict == "not_qualified":
        return "close_kindly" if applied else "explain_review"
    if verdict == "pool":
        return "offer_pool"
    return "explain_review"
