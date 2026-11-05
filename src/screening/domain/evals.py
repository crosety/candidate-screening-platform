from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from screening.domain.profile import RubricItem

# A simulated candidate stays silent for one platform reengagement turn.
SILENCE = "[SILENCIO]"


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,59}$")
    name: str
    facts: str
    style: str
    language: Literal["es", "en", "mixed"] = "es"
    expected_verdict: Literal["qualified", "not_qualified", "review", "pool"]
    must: list[str] = []
    must_not: list[str] = []


class JudgeItem(BaseModel):
    key: str
    score: int = Field(ge=0, le=100)
    comment: str


class JudgeCheck(BaseModel):
    text: str
    passed: bool
    evidence: str | None


class JudgeOutput(BaseModel):
    items: list[JudgeItem]
    checks: list[JudgeCheck]
    comment: str


def normalize_judge(output: JudgeOutput, rubric: list[RubricItem], persona: Persona) -> JudgeOutput:
    items = {item.key: item for item in output.items}
    checks = {item.text.strip(): item for item in output.checks}
    normalized = []
    for prefix, texts in (("Debe: ", persona.must), ("No debe: ", persona.must_not)):
        for text in texts:
            check = checks.get(prefix + text) or checks.get(text)
            normalized.append(
                JudgeCheck(
                    text=prefix + text,
                    passed=check.passed if check else False,
                    evidence=check.evidence if check else None,
                )
            )
    return JudgeOutput(
        items=[
            items.get(item.key, JudgeItem(key=item.key, score=0, comment="Sin valorar"))
            for item in rubric
        ],
        checks=normalized,
        comment=output.comment,
    )


def case_score(judge: JudgeOutput, rubric: list[RubricItem], verdict: str, expected: str) -> float:
    scores = {item.key: item.score for item in judge.items}
    weight = sum(item.weight for item in rubric)
    rubric_score = (
        sum(scores.get(item.key, 0) * item.weight for item in rubric) / weight if weight else 100
    )
    checks_score = (
        100 * sum(item.passed for item in judge.checks) / len(judge.checks) if judge.checks else 100
    )
    score = round(0.6 * rubric_score + 0.4 * checks_score, 1)
    return min(score, 40.0) if verdict != expected else score
