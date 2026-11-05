from datetime import datetime
from typing import Any, Literal

from ninja import Schema
from pydantic import Field

from screening.domain.profile import OfferDefinition, ResultField, Source


class WorkspaceRow(Schema):
    id: str
    application_id: str
    name: str
    phone: str
    offer_key: str
    offer_title: str
    status: str
    channel: str
    received_at: datetime
    started_at: datetime
    last_interaction_at: datetime
    handed_off_at: datetime | None
    owner: str | None
    attention: list[str]
    next_step: str
    last_message: str
    is_test: bool
    is_simulation: bool = False


class AwaitingApplication(Schema):
    id: str
    name: str
    phone: str
    offer_key: str
    received_at: datetime


class WorkspaceOut(Schema):
    rows: list[WorkspaceRow]
    awaiting: list[AwaitingApplication]
    total: int
    attention_count: int
    active_count: int
    completed_count: int
    applications_count: int
    awaiting_start_count: int


class FitItemOut(Schema):
    key: str
    label: str
    field: str
    operator: str
    expected: Any = None
    required: bool
    weight: float
    state: str
    value: Any = None
    evidence: str | None = None


class FitOut(Schema):
    score: int | None
    coverage: int
    requirements: str
    items: list[FitItemOut]


class WorkspaceMessage(Schema):
    id: str
    direction: str
    text: str
    channel: str
    created_at: datetime
    delivery_status: str = ""


class WorkspaceDecision(Schema):
    id: str
    decision_type: str
    status: str
    reason: str


class WorkspaceDetail(WorkspaceRow):
    messages: list[WorkspaceMessage]
    fields: dict[str, Any]
    field_definitions: list[ResultField]
    summary: str
    provisional: bool
    snapshot_at: datetime | None
    snapshot_stale: bool
    snapshot_status: str
    fit: FitOut
    handoff_reason: str
    decisions: list[WorkspaceDecision]
    version: str
    offer_description: str


class OfferOut(Schema):
    key: str
    title: str
    status: str
    description: str
    dimensions: dict[str, str]
    applications: int
    attention: int
    configured: bool
    draft: OfferDefinition | None = None
    published: OfferDefinition | None = None
    has_changes: bool = False
    application_url: str | None = None


class OffersOut(Schema):
    offers: list[OfferOut]
    rev: int | None = None
    shared_fields: list[ResultField]
    allowed_capabilities: list[str]
    sources: list[Source] = Field(default_factory=list)
    common_changes: bool = False
    schema_version: int = 2


class OfferPreviewOut(Schema):
    fields: list[ResultField]
    fields_origin: str
    sources: list[Source]
    capabilities: list[str]
    prompt: str
    affected_offers: list[str]


class SaveOfferIn(Schema):
    rev: int = Field(ge=1)
    definition: OfferDefinition


Environment = Literal["production", "simulation"]
