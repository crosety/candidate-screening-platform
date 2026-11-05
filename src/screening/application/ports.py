from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from screening.domain.channels import ChannelName, DeliveryReceipt, OutboundMessage
from screening.domain.evals import JudgeOutput, Persona
from screening.domain.profile import Connection, ProfileContent, ResultField
from screening.domain.result import EvaluationOutput
from screening.domain.run_context import RunContext
from screening.domain.signals import TurnSignal


class Clock(Protocol):
    def now(self) -> datetime: ...


class SecretStore(Protocol):
    def resolve(self, ref: str) -> str: ...


class ChannelAdapter(Protocol):
    channel: ChannelName

    async def send(self, message: OutboundMessage) -> DeliveryReceipt: ...


@dataclass(frozen=True)
class LlmUsage:
    tenant_id: UUID
    session_id: UUID | None
    eval_case_id: UUID | None
    agent: str
    model_id: str
    effort: str
    input_tokens: int  # sin los de caché
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd_micros: int
    latency_ms: int
    stop_reason: str


@dataclass(frozen=True)
class CapabilityCallRecord:
    tenant_id: UUID
    session_id: UUID | None
    capability: str
    connection_id: str
    status: str  # "ok", "error", "blocked"
    latency_ms: int
    request: dict[str, Any]
    response: dict[str, Any]
    eval_case_id: UUID | None = None


class BookingPolicy(Protocol):
    async def can_book(self, ctx: RunContext) -> bool: ...
    async def booked(
        self, ctx: RunContext, profile: ProfileContent, booking: dict[str, Any]
    ) -> None: ...


class Assessment(Protocol):
    async def complete(self, ctx: RunContext) -> dict[str, str]: ...


class TraceSink(Protocol):
    async def record_llm_call(self, usage: LlmUsage) -> None: ...
    async def record_capability_call(self, call: CapabilityCallRecord) -> None: ...


class MetricsDelivery(Protocol):
    """External metrics boundary. Snapshots contain only allowlisted measurements."""

    def export(self, spans: list[dict[str, Any]]) -> None: ...

    def received(self, spans: list[dict[str, Any]]) -> set[str]: ...
    def measure_voice(self, conversation_id: str, session: str) -> list[dict[str, Any]]: ...


class Connector(Protocol):
    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]: ...


class ConnectorRegistry(Protocol):
    def get(self, tenant_slug: str, connection: Connection) -> Connector: ...


@dataclass(frozen=True)
class DraftRecord:
    tenant_id: UUID
    rev: int
    content: ProfileContent
    base_version_id: UUID | None


@dataclass(frozen=True)
class VersionRecord:
    id: UUID
    tenant_id: UUID
    seq: int
    version: str
    content: ProfileContent
    content_hash: str
    risk: str
    note: str
    published_by_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class PublicationRecord:
    id: UUID
    tenant_id: UUID
    version_id: UUID
    previous_version_id: UUID | None
    percent: int
    state: str
    needs_second_approval: bool
    needs_client_approval: bool
    approvals: list[str]
    created_by_id: UUID
    created_at: datetime
    segment: str = ""


class ProfileRepository(Protocol):
    def locked(self, tenant_id: UUID) -> AbstractContextManager[None]: ...
    def list_publications(self, tenant_id: UUID) -> list[PublicationRecord]: ...
    def cancel_publication(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord: ...
    def was_activated(self, tenant_id: UUID, version_id: UUID) -> bool: ...
    def get_draft(self, tenant_id: UUID) -> DraftRecord: ...
    def save_draft(
        self, tenant_id: UUID, expected_rev: int, content: ProfileContent, user_id: UUID
    ) -> DraftRecord: ...
    def replace_draft(
        self, tenant_id: UUID, content: ProfileContent, base_version_id: UUID | None, user_id: UUID
    ) -> DraftRecord: ...
    def list_versions(self, tenant_id: UUID) -> list[VersionRecord]: ...
    def get_version(self, tenant_id: UUID, version_id: UUID) -> VersionRecord: ...
    def create_version(
        self, tenant_id: UUID, content: ProfileContent, risk: str, note: str, user_id: UUID
    ) -> VersionRecord: ...
    def active_publication(self, tenant_id: UUID) -> PublicationRecord | None: ...
    def create_publication(
        self,
        tenant_id: UUID,
        version_id: UUID,
        previous_version_id: UUID | None,
        percent: int,
        needs_second: bool,
        needs_client: bool,
        note: str,
        user_id: UUID,
        segment: str = "",
    ) -> PublicationRecord: ...
    def get_publication(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord: ...
    def add_approval(
        self, tenant_id: UUID, publication_id: UUID, kind: str, user_id: UUID
    ) -> PublicationRecord: ...
    def activate(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord: ...
    def tenant_flags(self, tenant_id: UUID) -> tuple[str, bool]: ...


@dataclass(frozen=True)
class TenantRecord:
    id: UUID
    slug: str
    name: str


@dataclass(frozen=True)
class NewApplication:
    external_id: str
    candidate_name: str
    phone: str
    city: str
    job_ref: str
    timezone: str = "Europe/Madrid"
    segment: str = ""
    is_test: bool = False


@dataclass(frozen=True)
class ApplicationRecord:
    id: UUID
    tenant_id: UUID
    external_id: str
    candidate_name: str
    phone: str
    city: str
    job_ref: str
    timezone: str
    segment: str = ""
    is_test: bool = False


@dataclass(frozen=True)
class LinkTarget:
    tenant_id: UUID
    application_id: UUID


@dataclass(frozen=True)
class SessionRecord:
    id: UUID
    tenant_id: UUID
    application_id: UUID
    profile_version_id: UUID
    channel: str
    status: str
    run_context: RunContext
    system_prompt: str
    created_at: datetime
    language: str = ""
    abandoned_stage: str = ""
    preferred_language: str = ""
    fallback_language: str = ""

    @property
    def response_language(self) -> str:
        return self.preferred_language or self.language or self.fallback_language


@dataclass(frozen=True)
class MessageRecord:
    id: UUID
    direction: str
    text: str
    created_at: datetime
    channel: str = "web"
    provider_message_id: str = ""
    delivery_status: str = ""
    simulated: bool = False
    client_message_id: str = ""
    language: str = ""
    signal: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SerializedMessage:
    role: str  # "user", "assistant", "tool", "system"
    content: dict[str, Any]  # opaco para el núcleo


class ConversationRepository(Protocol):
    async def get_tenant(self, tenant_id: UUID) -> TenantRecord: ...
    async def get_tenant_by_slug(self, slug: str) -> TenantRecord: ...  # NotFound
    async def upsert_application(
        self, tenant_id: UUID, data: NewApplication
    ) -> ApplicationRecord: ...
    async def get_application(self, tenant_id: UUID, application_id: UUID) -> ApplicationRecord: ...
    async def get_or_create_link(
        self, tenant_id: UUID, application_id: UUID
    ) -> str: ...  # devuelve el token
    async def resolve_link(self, token: str) -> LinkTarget: ...  # NotFound
    async def latest_session(
        self, tenant_id: UUID, application_id: UUID
    ) -> SessionRecord | None: ...
    async def create_session(
        self,
        tenant_id: UUID,
        session_id: UUID,
        application_id: UUID,
        profile_version_id: UUID,
        channel: str,
        run_context: RunContext,
        system_prompt: str,
    ) -> SessionRecord: ...
    async def get_session(self, tenant_id: UUID, session_id: UUID) -> SessionRecord: ...  # NotFound
    async def set_session_status(self, tenant_id: UUID, session_id: UUID, status: str) -> None: ...
    async def add_message(
        self,
        tenant_id: UUID,
        session_id: UUID,
        direction: str,
        text: str,
        client_message_id: str = "",
        *,
        channel: str = "web",
        provider_message_id: str = "",
        simulated: bool = False,
    ) -> MessageRecord | None: ...  # None si el client_message_id ya existe
    async def list_messages(self, tenant_id: UUID, session_id: UUID) -> list[MessageRecord]: ...
    async def tool_evidence(self, tenant_id: UUID, session_id: UUID) -> list[dict[str, Any]]: ...
    async def count_incoming(self, tenant_id: UUID, session_id: UUID) -> int: ...
    async def load_history(
        self, tenant_id: UUID, session_id: UUID
    ) -> list[SerializedMessage]: ...  # por seq
    async def append_history(
        self, tenant_id: UUID, session_id: UUID, start_seq: int, messages: list[SerializedMessage]
    ) -> None: ...  # Conflict si algún seq ya existe
    async def add_event(
        self, tenant_id: UUID, session_id: UUID | None, type: str, payload: dict[str, Any]
    ) -> None: ...
    async def record_signal(
        self, tenant_id: UUID, session_id: UUID, message_id: UUID, signal: TurnSignal
    ) -> None: ...  # also keeps the session's last observed language
    async def set_abandoned_stage(self, tenant_id: UUID, session_id: UUID, stage: str) -> None: ...
    async def set_language_choice(
        self, tenant_id: UUID, session_id: UUID, preferred: str, fallback: str
    ) -> None: ...


class SignalClassifier(Protocol):
    """Cheap per-turn observations; failures must never block the conversation."""

    async def classify(self, ctx: RunContext, text: str) -> TurnSignal: ...
    async def pending_stage(
        self, ctx: RunContext, question: str, fields: list[ResultField]
    ) -> str: ...  # a field key, "summary", "interview" or "" when unclear


class ProfileVersions(Protocol):
    async def draft(self, tenant_id: UUID) -> DraftRecord: ...
    async def active(self, tenant_id: UUID) -> VersionRecord | None: ...
    async def for_new_session(
        self, tenant_id: UUID, session_id: UUID, segment: str = ""
    ) -> VersionRecord: ...
    async def get(self, tenant_id: UUID, version_id: UUID) -> VersionRecord: ...


@dataclass(frozen=True)
class TurnRequest:
    run_context: RunContext
    profile: ProfileContent
    system_prompt: str
    history: list[SerializedMessage]
    text: str | None  # None = primer turno
    turn_note: str
    channel: str = "web"
    tenant_name: str = ""


@dataclass(frozen=True)
class TurnResult:
    new_messages: list[SerializedMessage]
    reply: str | None
    flags: frozenset[str]  # "guard_failed", "limit_reached"


class GuardVerdict(BaseModel):
    approved: bool
    reason: str


class ReplyGuard(Protocol):
    async def review(self, ctx: RunContext, reply: str, sources: str) -> GuardVerdict: ...


class AgentRunner(Protocol):
    async def run_turn(self, request: TurnRequest) -> TurnResult: ...


@dataclass(frozen=True)
class ResultRecord:
    session_id: UUID
    verdict: str
    fields: dict[str, Any]
    flags: list[str]
    summary: str
    reason: str
    evaluator_model: str
    sentiment: list[dict[str, str | None]] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionRecord:
    id: UUID
    tenant_id: UUID
    session_id: UUID
    decision_type: str
    status: str
    autonomy_level: int
    decided_by: str
    user_id: UUID | None
    reason: str
    created_at: datetime
    applied_at: datetime | None


@dataclass(frozen=True)
class OutboxRecord:
    id: UUID
    tenant_id: UUID
    session_id: UUID
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int


class DecisionRepository(Protocol):
    async def get_result(self, tenant_id: UUID, session_id: UUID) -> ResultRecord | None: ...
    async def save_result(self, tenant_id: UUID, result: ResultRecord) -> None: ...
    async def create_decision(
        self,
        tenant_id: UUID,
        session_id: UUID,
        decision_type: str,
        status: str,
        autonomy_level: int,
        decided_by: str,
        user_id: UUID | None,
        reason: str,
        applied_at: datetime | None,
    ) -> DecisionRecord: ...
    async def get_decision(self, tenant_id: UUID, decision_id: UUID) -> DecisionRecord: ...
    async def set_decision_status(
        self,
        tenant_id: UUID,
        decision_id: UUID,
        status: str,
        user_id: UUID | None,
        applied_at: datetime | None,
    ) -> DecisionRecord: ...
    async def list_proposed(self, tenant_id: UUID) -> list[DecisionRecord]: ...
    async def has_applied_qualify(self, tenant_id: UUID, session_id: UUID) -> bool: ...
    async def create_outbox(
        self,
        tenant_id: UUID,
        session_id: UUID,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboxRecord: ...
    async def get_outbox(self, tenant_id: UUID, item_id: UUID) -> OutboxRecord: ...
    async def mark_outbox(
        self,
        tenant_id: UUID,
        item_id: UUID,
        status: str,
        error: str = "",
    ) -> None: ...


class JobQueue(Protocol):
    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        tenant_id: UUID | None,
        idempotency_key: str | None = None,
        max_attempts: int = 5,
    ) -> None: ...


@dataclass(frozen=True)
class EvaluationRequest:
    run_context: RunContext
    profile: ProfileContent
    transcript: list[MessageRecord]
    tool_evidence: list[dict[str, Any]] = field(default_factory=list)


class Evaluator(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationOutput: ...


@dataclass(frozen=True)
class EvalRunRecord:
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
    alternative_content: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvalCaseRecord:
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


class EvalRepository(Protocol):
    async def create_run(
        self,
        tenant_id: UUID,
        draft_rev: int,
        published_version_id: UUID | None,
        persona_keys: list[str],
        compare_published: bool,
        max_cost_usd_micros: int,
        user_id: UUID,
    ) -> EvalRunRecord: ...
    async def get_run(self, tenant_id: UUID, run_id: UUID) -> EvalRunRecord: ...
    async def list_runs(self, tenant_id: UUID) -> list[EvalRunRecord]: ...
    async def set_run(self, tenant_id: UUID, run_id: UUID, **changes: Any) -> None: ...
    async def create_case(
        self, tenant_id: UUID, run_id: UUID, persona_key: str, target: str
    ) -> EvalCaseRecord: ...
    async def list_cases(self, tenant_id: UUID, run_id: UUID) -> list[EvalCaseRecord]: ...
    async def finish_case(
        self,
        tenant_id: UUID,
        case_id: UUID,
        transcript: list[dict[str, str]],
        judge: dict[str, Any],
        verdict: str,
        score: float | None,
        status: str,
        cost_usd_micros: int,
    ) -> None: ...
    async def claim_run(self, tenant_id: UUID, run_id: UUID) -> bool: ...
    async def case_cost(self, tenant_id: UUID, case_id: UUID) -> int: ...


class PersonaSource(Protocol):
    def list_personas(self, tenant_slug: str, offer_key: str = "") -> list[Persona]: ...


class CandidateSimulator(Protocol):
    async def next_message(
        self, ctx: RunContext, persona: Persona, transcript: list[MessageRecord]
    ) -> str | None: ...


class Judge(Protocol):
    async def judge(
        self,
        ctx: RunContext,
        profile: ProfileContent,
        persona: Persona,
        transcript: list[MessageRecord],
        result: ResultRecord | None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> JudgeOutput: ...


@dataclass(frozen=True)
class ConnectorStatusRecord:
    tenant_id: UUID
    connection_id: str
    summary: str
    warnings: list[dict[str, str]]
    checked_at: datetime


class ConnectorStatusRepository(Protocol):
    async def upsert(
        self,
        tenant_id: UUID,
        connection_id: str,
        summary: str,
        warnings: list[dict[str, str]],
        checked_at: datetime,
    ) -> None: ...
    async def list_statuses(self, tenant_id: UUID) -> list[ConnectorStatusRecord]: ...
    async def last_checked(self, tenant_id: UUID) -> datetime | None: ...


class Suggestion(BaseModel):
    field: Literal["role", "who", "fit", "voice", "followup", "avoid", "operating_model"]
    why: str
    append: str
    evidence_ids: list[str] = []


class Copilot(Protocol):
    async def suggest(self, ctx: RunContext, section: str, material: str) -> list[Suggestion]: ...


@dataclass(frozen=True)
class CopilotMaterial:
    sessions: list[dict[str, Any]]
    corrections: list[dict[str, Any]]
    eval_cases: list[dict[str, Any]]


class CopilotMaterialReader(Protocol):
    async def recent(self, tenant_id: UUID) -> CopilotMaterial: ...
