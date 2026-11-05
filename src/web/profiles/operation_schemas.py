"""Typed response envelopes for phase-two operator APIs."""

from datetime import date, datetime
from uuid import UUID

from ninja import Schema

from web.profiles.schemas import DiffOut


class EvidenceOut(Schema):
    id: UUID
    session_id: UUID
    direction: str
    text: str
    url: str


class ImprovementOut(Schema):
    id: UUID
    base_rev: int
    state: str
    rationale: str
    diff: DiffOut
    evidence: list[EvidenceOut]
    eval_run_id: UUID | None
    created_at: datetime
    accepted_rev: int | None


class ProposalIdsOut(Schema):
    ids: list[UUID]


class ProposalActionOut(Schema):
    rev: int | None = None
    state: str | None = None
    eval_run_id: UUID | None = None


class PolicyOut(Schema):
    nightly_enabled: bool
    improvement_enabled: bool
    timezone: str
    nightly_hour: int
    judge_percent: int
    daily_budget_micros: int
    run_budget_micros: int


class ReservationOut(Schema):
    id: UUID
    kind: str
    reserved_micros: int
    actual_micros: int
    state: str
    day: date


class MaintenanceOut(Schema):
    policy: PolicyOut
    reservations: list[ReservationOut]


class PilotOut(Schema):
    id: UUID
    segment: str
    version_id: UUID
    baseline_id: UUID
    percent: int
    enabled: bool


class PilotMetricOut(Schema):
    application__segment: str
    profile_version__version: str
    status: str
    count: int


class PilotsOut(Schema):
    pilots: list[PilotOut]
    metrics: list[PilotMetricOut]
    note: str


class FaqStatusOut(Schema):
    version_id: UUID
    version: str
    fragments: int
    models: list[str]


class FaqIndexOut(Schema):
    fragments: int


class FaqFragmentOut(Schema):
    source_id: str
    source_name: str
    text: str
    score: float
    question: str = ""
    citation: str = ""


class FaqSearchOut(Schema):
    status: str
    fragments: list[FaqFragmentOut]
    instruction: str | None = None
