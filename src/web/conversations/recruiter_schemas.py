from datetime import date, datetime
from typing import Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field

from screening.domain.result import FieldValue, StageSentiment


class CandidateRowOut(Schema):
    id: UUID
    application__candidate_name: str
    application__segment: str
    status: str
    profile_version__version: str
    created_at: datetime


class CandidatesOut(Schema):
    total: int
    items: list[CandidateRowOut]


class MessageSignalOut(Schema):
    """Tone observation for one candidate message; never a hiring criterion."""

    mood: str = ""
    quote: str = ""
    tone: str = ""
    code_switching: bool = False


class CandidateMessageOut(Schema):
    id: UUID | None = None
    direction: str
    text: str
    channel: str
    created_at: datetime
    language: str = ""
    signal: MessageSignalOut | None = None


class CandidateResultOut(Schema):
    verdict: str
    summary: str
    fields: dict[str, FieldValue]
    flags: list[str]
    sentiment: list[StageSentiment]


class CandidateDecisionOut(Schema):
    decision_type: str
    status: str
    reason: str


class CandidateTimelineOut(Schema):
    id: UUID
    type: str
    created_at: datetime


class CandidateFollowupOut(Schema):
    id: UUID
    status: str
    run_at: datetime


class CandidateDetailOut(Schema):
    id: UUID
    name: str
    segment: str
    status: str
    version: str
    messages: list[CandidateMessageOut]
    result: CandidateResultOut | None
    decisions: list[CandidateDecisionOut]
    timeline: list[CandidateTimelineOut]
    followups: list[CandidateFollowupOut]
    next_step: str


class QueryPayloadOut(Schema):
    question: str
    reply: str
    segments: list[str]


class QueryHistoryOut(Schema):
    id: UUID
    created_at: datetime
    payload: QueryPayloadOut


class CountOut(Schema):
    label: str
    count: int


class CountsOut(Schema):
    group: Literal[
        "status", "segment", "version", "verdict", "channel", "offer", "language", "sentiment"
    ]
    items: list[CountOut]
    unit: str | None = None


class AgreementItemOut(Schema):
    version: str
    reviewed: int
    agree: int
    agreement_pct: float | None


class AgreementOut(Schema):
    group: Literal["agreement"]
    items: list[AgreementItemOut]
    note: str


class OutcomeCountsOut(Schema):
    """Rates use closed conversations (completed + abandoned + handed off) as denominator."""

    total: int
    in_progress: int = 0
    closed: int = 0
    completed: int = 0
    abandoned: int = 0
    handed_off: int = 0
    completion_pct: float | None
    abandonment_pct: float | None
    handoff_pct: float | None = None
    median_duration_seconds: float | None = None
    p90_duration_seconds: float | None = None


class BreakdownOut(OutcomeCountsOut):
    key: str
    label: str


class FunnelStepOut(Schema):
    key: str
    label: str
    count: int
    pct: float | None


class StageOut(Schema):
    key: str
    label: str
    order: int
    count: int
    pct: float | None
    moods: list[CountOut] = Field(default_factory=list)


class DayOut(Schema):
    day: date
    started: int
    completed: int
    abandoned: int


class MoodQuoteOut(Schema):
    session_id: UUID
    message_id: UUID
    quote: str
    created_at: datetime


class MoodCountOut(Schema):
    mood: str
    count: int
    quotes: list[MoodQuoteOut] = Field(default_factory=list)


class MoodPhaseOut(Schema):
    key: str
    label: str
    total: int
    items: list[MoodCountOut]


class DefinitionOut(Schema):
    key: str
    text: str


class MetricsOut(OutcomeCountsOut):
    group: Literal["metrics"]
    mean_duration_seconds: float | None
    duration_samples: int = 0
    mean_first_response_seconds: float | None
    median_first_response_seconds: float | None = None
    p90_first_response_seconds: float | None = None
    first_response_samples: int
    # Legacy raw stage keys; `stages` carries labels, order and observed mood.
    abandonment_stages: list[CountOut]
    followup_recipients: int
    responded_after_followup: int
    response_pct: float | None
    note: str
    funnel: list[FunnelStepOut] = Field(default_factory=list)
    stages: list[StageOut] = Field(default_factory=list)
    by_channel: list[BreakdownOut] = Field(default_factory=list)
    by_offer: list[BreakdownOut] = Field(default_factory=list)
    by_language: list[BreakdownOut] = Field(default_factory=list)
    series: list[DayOut] = Field(default_factory=list)
    moods: list[MoodPhaseOut] = Field(default_factory=list)
    quotes_visible: bool = False
    definitions: list[DefinitionOut] = Field(default_factory=list)
