import math
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from screening.domain.profile import ResultField

Verdict = Literal["qualified", "not_qualified", "review", "pool"]


class FieldValue(BaseModel):
    value: StrictStr | StrictBool | StrictInt | StrictFloat | None
    evidence: str | None
    confidence: Literal["high", "medium", "low"]


# Evaluators sometimes join two literal quotes ("El lunes que viene. / Sí, todo correcto.");
# each fragment must still be the candidate's own words, so commentary never passes.
_QUOTE_SEPARATOR = re.compile(r"\s+/\s+|\s*(?:\[\.\.\.\]|\[…\]|…)\s*")
_QUOTE_EDGES = " \t\n«»“”\"'.,;:"


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def quoted_in(evidence: str | None, texts: list[str]) -> bool:
    """True when every fragment of the evidence is a literal quote from one of the texts."""
    if not evidence or not evidence.strip():
        return False
    haystacks = [_normalized(text) for text in texts]
    if any(evidence in text for text in texts):
        return True
    fragments = [_normalized(part.strip(_QUOTE_EDGES)) for part in _QUOTE_SEPARATOR.split(evidence)]
    return all(
        len(fragment) >= 2 and any(fragment in haystack for haystack in haystacks)
        for fragment in fragments
    )


def invalid_field(field: ResultField, item: FieldValue, today: date) -> bool:
    value = item.value
    if value is None or value == "":
        return field.required
    if field.value_type == "boolean":
        return not isinstance(value, bool)
    if field.value_type == "number":
        return (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or (field.minimum is not None and value < field.minimum)
        )
    if field.value_type == "enum":
        return value not in field.choices
    if field.value_type == "date":
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError:
            return True
        return field.future_date and parsed < today
    return not isinstance(value, str)


class StageSentiment(BaseModel):
    stage: Literal["opening", "requirements", "matching", "closing"]
    label: Literal["positive", "neutral", "frustrated", "unclear"]
    evidence: str | None


class EvaluationOutput(BaseModel):
    verdict: Verdict
    fields: dict[str, FieldValue]
    flags: list[
        Literal[
            "prompt_injection_attempt",
            "third_party_data_request",
            "sensitive_data_shared",
            "unclear_requirement",
        ]
    ]
    reason: str
    summary: str
    sentiment: list[StageSentiment] = Field(default_factory=list, max_length=4)


def observed_sentiment(
    items: list[StageSentiment], candidate_texts: list[str]
) -> list[dict[str, str | None]]:
    result: list[dict[str, str | None]] = []
    seen = set()
    for item in items:
        if item.stage in seen:
            continue
        seen.add(item.stage)
        if quoted_in(item.evidence, candidate_texts):
            result.append(item.model_dump())
        else:
            result.append({"stage": item.stage, "label": "unclear", "evidence": None})
    return result
