from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ninja import Schema
from pydantic import Field, field_validator

SessionStatus = Literal["active", "handed_off", "completed", "abandoned"]


class NewApplicationIn(Schema):
    segment: str = Field(default="", max_length=60)
    external_id: str = Field(min_length=1, max_length=60)
    candidate_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(max_length=30)
    city: str = Field(max_length=120)
    job_ref: str = Field(max_length=60)
    timezone: str = Field(default="Europe/Madrid", max_length=40)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Zona horaria no válida") from exc
        return value


class IngestOut(Schema):
    application_id: UUID
    candidate_link: str


class ChatSessionIn(Schema):
    token: str = Field(min_length=1, max_length=64)
    language: Literal["", "es", "en"] = ""
    fallback_language: Literal["", "es", "en"] = ""


class OpenedSessionOut(Schema):
    websocket_token: str
    session_id: UUID
    tenant_name: str
    status: SessionStatus
    offer_title: str
    is_test: bool
    # Explicit choice, then observed language, then browser fallback; "" until known.
    language: str = ""


class NewAtsRecordIn(Schema):
    candidate_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(max_length=30)
    city: str = Field(max_length=120)
    job_ref: str = Field(max_length=60)


class NewAtsRecordOut(Schema):
    external_id: str
    candidate_link: str


class AtsRecordOut(Schema):
    id: UUID
    external_id: str
    candidate_name: str
    phone: str
    city: str
    job_ref: str
    stage: str
    fields: dict[str, Any]
    notes: list[Any]
    updated_at: datetime
    candidate_link: str | None


class SessionListOut(Schema):
    id: UUID
    candidate_name: str
    status: SessionStatus
    version: str
    created_at: datetime
    messages: int
    cost_usd_micros: int


class SessionOut(Schema):
    id: UUID
    status: SessionStatus
    channel: str
    created_at: datetime
    ended_at: datetime | None


class ApplicationOut(Schema):
    id: UUID
    external_id: str
    candidate_name: str
    phone: str
    city: str
    job_ref: str
    timezone: str


class SessionVersionOut(Schema):
    id: UUID
    version: str


class MessageOut(Schema):
    channel: str = "web"
    client_message_id: str = ""
    id: UUID
    direction: Literal["in", "out"]
    text: str
    created_at: datetime


class LlmCallOut(Schema):
    id: UUID
    agent: str
    model_id: str
    effort: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd_micros: int
    latency_ms: int
    stop_reason: str


class CapabilityCallOut(Schema):
    id: UUID
    capability: str
    connection_id: str
    status: Literal["ok", "error", "blocked"]
    latency_ms: int
    request: dict[str, Any]
    response: dict[str, Any]


class SessionDetailOut(Schema):
    langfuse_url: str | None
    session: SessionOut
    application: ApplicationOut
    version: SessionVersionOut
    messages: list[MessageOut]
    llm_calls: list[LlmCallOut]
    capability_calls: list[CapabilityCallOut]
    result: dict[str, Any] | None
    decisions: list[dict[str, Any]]
    cost_usd_micros: int
