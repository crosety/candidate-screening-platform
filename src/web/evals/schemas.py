from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Schema


class EvalIn(Schema):
    offer_key: str = ""
    persona_keys: list[str] | None = None
    compare_published: bool = True
    max_cost_usd: float = 3


class EvalCaseOut(Schema):
    id: UUID
    run_id: UUID
    persona_key: str
    target: str
    transcript: list[dict[str, str]]
    judge: dict[str, Any]
    verdict: str
    score: float | None
    cost_usd_micros: int
    status: str


class EvalRunOut(Schema):
    id: UUID
    tenant_id: UUID
    draft_rev: int
    published_version_id: UUID | None
    status: str
    persona_keys: list[str]
    compare_published: bool
    max_cost_usd_micros: int
    score_draft: float | None
    score_published: float | None
    summary: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None
    cost_usd_micros: int


class EvalRunDetailOut(EvalRunOut):
    cases: list[EvalCaseOut]


class GateOut(Schema):
    required: bool
    passed: bool
    run_id: str | None
    score: float | None
    threshold: float
