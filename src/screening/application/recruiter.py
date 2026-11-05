from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from statistics import median, quantiles
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from screening.domain.profile import FieldSemantic, ProfileContent, profile_for_offer
from screening.domain.run_context import RunContext


@dataclass(frozen=True)
class RecruiterScope:
    tenant_id: UUID
    user_id: UUID
    segments: tuple[str, ...]
    # Literal candidate quotes are returned only to people who can open these
    # conversations; the read-only assistant receives counts without quotes.
    conversations: bool = False


class CandidateQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["", "active", "completed", "handed_off", "abandoned"] = ""
    segment: str = Field(default="", max_length=60)
    verdict: Literal["", "qualified", "not_qualified", "review", "pool"] = ""
    location: str = Field(default="", max_length=120)
    shift: str = Field(default="", max_length=60)
    channel: Literal["", "web", "whatsapp", "voice", "telephone", "sim"] = ""
    since: date | None = None
    until: date | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)


class AggregateQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: Literal[
        "status",
        "segment",
        "version",
        "verdict",
        "channel",
        "offer",
        "language",
        "sentiment",
        "metrics",
        "agreement",
    ] = "status"
    segment: str = Field(default="", max_length=60)
    channel: Literal["", "web", "whatsapp", "voice", "telephone", "sim"] = ""
    offer: str = Field(default="", max_length=60)
    # "unknown" selects conversations whose language has not been observed yet.
    language: str = Field(default="", pattern=r"^(?:[a-z]{2}|unknown)?$")
    # "" keeps every application in scope; the console separates operation from simulations.
    environment: Literal["", "production", "simulation"] = ""
    since: date | None = None
    until: date | None = None


class RecruiterReader(Protocol):
    async def query(self, scope: RecruiterScope, query: CandidateQuery) -> dict[str, Any]: ...
    async def aggregates(self, scope: RecruiterScope, query: AggregateQuery) -> dict[str, Any]: ...


class RecruiterAssistant(Protocol):
    async def answer(
        self, ctx: RunContext, scope: RecruiterScope, question: str, reader: RecruiterReader
    ) -> str: ...


# Filters by platform meaning, so no client field key is hardcoded in the platform.
LOCATION_SEMANTICS: tuple[FieldSemantic, ...] = ("site", "location")
SHIFT_SEMANTICS: tuple[FieldSemantic, ...] = ("shift",)

# Pending steps that are not result fields; legacy values come from older close events.
STEP_LABELS = {
    "interview": "Cita de entrevista",
    "summary": "Resumen final",
    "awaiting_candidate": "Esperando respuesta del candidato",
    "": "Sin etapa registrada",
}
STEP_ORDER = {"interview": 1000, "summary": 1001, "awaiting_candidate": 1002, "": 1003}


def semantic_keys(
    profile: ProfileContent | None, offer_key: str, semantics: Iterable[str]
) -> list[str]:
    """Result keys with a platform meaning in the session's frozen version and offer."""
    if profile is None:
        return []
    wanted = set(semantics)
    schema = profile_for_offer(profile, offer_key).result_schema
    return [item.key for item in schema if item.semantic in wanted]


def stage_label(profile: ProfileContent | None, offer_key: str, key: str) -> tuple[str, int]:
    """Human label and position of a pending stage; the field key is the fallback label."""
    if profile is not None:
        schema = profile_for_offer(profile, offer_key).result_schema
        for index, item in enumerate(schema):
            if item.key == key:
                return item.label or key, index
    if key in STEP_LABELS:
        return STEP_LABELS[key], STEP_ORDER[key]
    return key, STEP_ORDER[""] - 1


def spread(values: list[float]) -> tuple[float | None, float | None]:
    """Median and 90th percentile (linear interpolation, inclusive method)."""
    if not values:
        return None, None
    if len(values) == 1:
        return round(values[0], 1), round(values[0], 1)
    return round(median(values), 1), round(quantiles(values, n=10, method="inclusive")[-1], 1)


def rate(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def phase(position: int, count: int, closed: bool) -> Literal["opening", "middle", "closing"]:
    """Conversation phase of the n-th candidate message (0-based) among `count` messages."""
    if position == 0:
        return "opening"
    if closed and position == count - 1:
        return "closing"
    return "middle"
