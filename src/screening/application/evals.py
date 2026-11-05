import asyncio
import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from statistics import mean
from typing import Any
from uuid import UUID, uuid4

from screening.application.assessment import AssessmentService, DecisionBookingPolicy
from screening.application.capability_gateway import CapabilityGateway
from screening.application.conversation import ConversationService
from screening.application.faq import FaqService, MemoryFaqStore
from screening.application.interviews import InterviewService
from screening.application.memory import (
    FixedProfileVersions,
    InMemoryConversationRepository,
    InMemoryDecisionRepository,
    InMemoryInterviewStore,
    InMemoryJobQueue,
)
from screening.application.ports import (
    AgentRunner,
    CandidateSimulator,
    CapabilityCallRecord,
    Clock,
    Connector,
    ConnectorRegistry,
    ConversationRepository,
    EvalCaseRecord,
    EvalRepository,
    EvalRunRecord,
    Evaluator,
    JobQueue,
    Judge,
    LlmUsage,
    NewApplication,
    PersonaSource,
    ProfileVersions,
    SignalClassifier,
    TraceSink,
    VersionRecord,
)
from screening.domain.errors import NotAllowed, NotFound
from screening.domain.eval_identity import eval_snapshot
from screening.domain.evals import SILENCE, Persona, case_score
from screening.domain.languages import language_variants
from screening.domain.profile import Connection, ProfileContent, profile_for_offer
from screening.domain.reengagement import reminder_due
from screening.domain.run_context import RunContext

RunnerFactory = Callable[[CapabilityGateway], AgentRunner]


@dataclass
class _EvalCalendar:
    """Per-case snapshot: provider reads are allowed, every calendar write stays here."""

    clock: Clock
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)
    ledger: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)

    def available(self, result: dict[str, Any]) -> dict[str, Any]:
        slots = result.get("slots", [])
        self.slots.update({str(slot["slot_id"]): deepcopy(slot) for slot in slots})
        return {
            **result,
            "slots": [slot for slot in slots if not self.owners.get(str(slot["slot_id"]))],
        }

    def write(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("idempotency_key") or "")
        if key and key in self.ledger:
            previous_operation, result = self.ledger[key]
            if previous_operation != operation:
                raise NotAllowed("Clave de idempotencia reutilizada en otra operación.")
            return {**deepcopy(result), "replayed": True}
        # There is no await inside this state transition, so concurrent cases cannot
        # interleave a half-move. Each case/connection has its own state and ledger.
        result = self._apply(operation, args)
        if key and result.get("status") in {"booked", "rescheduled", "cancelled"}:
            self.ledger[key] = (operation, deepcopy(result))
        return result

    def _apply(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        owner = str(args["application_external_id"])
        slot_id = str(args["slot_id"])
        if operation == "book_slot" and owner in self.owners.values():
            return {"status": "already_booked"}
        new_id = str(args.get("new_slot_id", ""))
        if operation == "reschedule_booking" and slot_id == new_id:
            return {"status": "same_slot"}
        if slot_id not in self.slots or (
            operation == "reschedule_booking" and new_id not in self.slots
        ):
            if operation == "reschedule_booking":
                return {"status": "not_found"}
            raise NotFound("Hueco no encontrado")
        if operation != "book_slot" and self.owners.get(slot_id) != owner:
            return {"status": "not_owner"}
        if operation == "cancel_booking":
            self.owners.pop(slot_id)
            return {"status": "cancelled", "slot_id": slot_id}
        target = new_id if operation == "reschedule_booking" else slot_id
        slot = self.slots[target]
        if datetime.fromisoformat(str(slot["starts_at"])) <= self.clock.now():
            return {"status": "expired"}
        if self.owners.get(target):
            return {"status": "taken"}
        self.owners[target] = owner
        if operation == "reschedule_booking":
            self.owners.pop(slot_id)
            return {"status": "rescheduled", "previous_slot_id": slot_id, **deepcopy(slot)}
        return {"status": "booked", **deepcopy(slot)}


class _EvalConnector:
    def __init__(
        self,
        inner: ConnectorRegistry,
        slug: str,
        connection: Connection,
        persona: Persona,
        calendar: _EvalCalendar,
    ) -> None:
        self.inner, self.slug, self.connection, self.persona = inner, slug, connection, persona
        self.calendar = calendar

    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        if operation == "get_application":
            return {"candidate_name": self.persona.name, "city": "", "job_ref": "eval"}
        if operation in ("book_slot", "reschedule_booking", "cancel_booking"):
            return self.calendar.write(operation, args)
        if operation in ("write_result", "set_stage"):
            return {"status": "ok"}
        if operation not in ("find_openings", "list_slots", "verify_credential"):
            raise NotAllowed("Operación no permitida en evals")
        result = await self.inner.get(self.slug, self.connection).call(operation, args)
        if operation == "list_slots":
            return self.calendar.available(result)
        return result


class EvalConnectorRegistry:
    def __init__(self, inner: ConnectorRegistry, persona: Persona, clock: Clock) -> None:
        self.inner, self.persona, self.clock = inner, persona, clock
        self.calendars: dict[tuple[str, str], _EvalCalendar] = {}

    def get(self, tenant_slug: str, connection: Connection) -> Connector:
        calendar = self.calendars.setdefault(
            (tenant_slug, connection.id), _EvalCalendar(self.clock)
        )
        return _EvalConnector(self.inner, tenant_slug, connection, self.persona, calendar)


# The same successful reads production exposes to the evaluator (tool_evidence).
EVIDENCE_CAPABILITIES = ("openings.find", "credential.verify", "application.read_mine")


class _EvidenceSink:
    """Keeps traces durable and mirrors tool evidence into the isolated eval repository."""

    def __init__(self, inner: TraceSink, repo: InMemoryConversationRepository) -> None:
        self.inner, self.repo = inner, repo
        # The judge checks every successful tool result, including interview slots/bookings.
        self.judge_evidence: list[dict[str, Any]] = []

    async def record_llm_call(self, usage: LlmUsage) -> None:
        await self.inner.record_llm_call(usage)

    async def record_capability_call(self, call: CapabilityCallRecord) -> None:
        await self.inner.record_capability_call(call)
        if call.status == "ok" and not call.capability.startswith("platform."):
            self.judge_evidence.append(
                {"capability": call.capability, "request": call.request, "response": call.response}
            )
        if call.session_id and call.status == "ok" and call.capability in EVIDENCE_CAPABILITIES:
            self.repo.evidence.setdefault((call.tenant_id, call.session_id), []).append(
                {"capability": call.capability, "request": call.request, "response": call.response}
            )


class EvalService:
    CONCURRENCY = 3
    MAX_CANDIDATE_TURNS = 12

    def __init__(
        self,
        evals: EvalRepository,
        versions: ProfileVersions,
        personas: PersonaSource,
        tenants: ConversationRepository,
        registry: ConnectorRegistry,
        runner_factory: RunnerFactory,
        evaluator: Evaluator,
        simulator: CandidateSimulator,
        judge: Judge,
        sink: TraceSink,
        clock: Clock,
        queue: JobQueue,
        evaluator_model: str,
        harness: dict[str, str] | None = None,
        faq: FaqService | None = None,
        signals: SignalClassifier | None = None,
    ) -> None:
        self.evals, self.versions, self.personas, self.tenants = evals, versions, personas, tenants
        self.registry, self.runner_factory, self.evaluator = registry, runner_factory, evaluator
        self.simulator, self.judge, self.sink, self.clock = simulator, judge, sink, clock
        self.queue, self.evaluator_model = queue, evaluator_model
        self.harness = harness or {"mode": "fake"}
        self.faq = faq
        self.signals = signals

    async def start(
        self,
        tenant_id: UUID,
        persona_keys: list[str] | None,
        compare_published: bool,
        max_cost_usd: float,
        user_id: UUID,
        enqueue: bool = True,
        alternative: ProfileContent | None = None,
        offer_key: str = "",
    ) -> EvalRunRecord:
        tenant = await self.tenants.get_tenant(tenant_id)
        personas = self.personas.list_personas(tenant.slug, offer_key)
        if persona_keys is not None:
            personas = [*personas, *language_variants(personas)]
            unknown = set(persona_keys) - {p.key for p in personas}
            if unknown:
                raise NotAllowed(f"Persona desconocida: {sorted(unknown)[0]}")
            personas = [p for p in personas if p.key in persona_keys]
        if not personas:
            raise NotAllowed("Este cliente no tiene personas para evals.")
        if not 0.1 <= max_cost_usd <= 20:
            raise NotAllowed("El coste debe estar entre 0,1 y 20 USD.")
        draft = await self.versions.draft(tenant_id)
        content = alternative or draft.content
        if (offer_key and offer_key not in {o.key for o in content.offers}) or (
            not offer_key and any(o.status == "open" for o in content.offers)
        ):
            raise NotAllowed("Selecciona la oferta que quieres evaluar.")
        active = await self.versions.active(tenant_id) if compare_published else None
        run = await self.evals.create_run(
            tenant_id,
            draft.rev,
            active.id if active else None,
            [p.key for p in personas],
            compare_published,
            round(max_cost_usd * 1000000),
            user_id,
        )
        await self.evals.set_run(
            tenant_id,
            run.id,
            summary={
                "harness": self.harness,
                **eval_snapshot(alternative or draft.content, personas),
                "offer_key": offer_key,
            },
        )
        for persona in personas:
            await self.evals.create_case(tenant_id, run.id, persona.key, "draft")
            if active:
                await self.evals.create_case(tenant_id, run.id, persona.key, "published")
        if alternative is not None:
            await self.evals.set_run(
                tenant_id, run.id, alternative_content=alternative.model_dump(mode="json")
            )
        if enqueue:
            await self.queue.enqueue(
                "eval_run", {"run_id": str(run.id)}, tenant_id, f"eval_run:{run.id}", max_attempts=1
            )
        return await self.evals.get_run(tenant_id, run.id)

    async def execute(self, tenant_id: UUID, run_id: UUID) -> None:
        if not await self.evals.claim_run(tenant_id, run_id):
            return
        try:
            await self._execute(tenant_id, run_id)
        except BaseException as exc:
            await self.evals.set_run(
                tenant_id,
                run_id,
                status="failed",
                last_error=type(exc).__name__,
                finished_at=self.clock.now(),
            )
            raise

    async def _execute(self, tenant_id: UUID, run_id: UUID) -> None:
        run = await self.evals.get_run(tenant_id, run_id)
        cases = await self.evals.list_cases(tenant_id, run_id)
        semaphore = asyncio.Semaphore(self.CONCURRENCY)

        async def guarded(case: EvalCaseRecord) -> None:
            async with semaphore:
                cost = sum([await self.evals.case_cost(tenant_id, c.id) for c in cases])
                if cost >= run.max_cost_usd_micros:
                    await self.evals.finish_case(
                        tenant_id,
                        case.id,
                        [],
                        {"error": "Presupuesto agotado"},
                        "",
                        None,
                        "error",
                        0,
                    )
                    return
                await self._case(tenant_id, run, case)

        await asyncio.gather(*(guarded(case) for case in cases if case.status == "pending"))
        completed = await self.evals.list_cases(tenant_id, run_id)
        scores = {
            target: [
                c.score
                for c in completed
                if c.target == target and c.status == "done" and c.score is not None
            ]
            for target in ("draft", "published")
        }
        errors = [c for c in completed if c.status == "error"]
        summary = {
            **run.summary,
            "cases": len(completed),
            "done": sum(c.status == "done" for c in completed),
            "errors": len(errors),
            "cost_usd_micros": sum(c.cost_usd_micros for c in completed),
            "by_persona": [
                {
                    "persona_key": key,
                    **{
                        target: next(
                            (
                                c.score
                                for c in completed
                                if c.persona_key == key and c.target == target
                            ),
                            None,
                        )
                        for target in ("draft", "published")
                    },
                }
                for key in run.persona_keys
            ],
        }
        await self.evals.set_run(
            tenant_id,
            run_id,
            status="failed" if errors else "done",
            score_draft=mean(scores["draft"]) if scores["draft"] else None,
            score_published=mean(scores["published"]) if scores["published"] else None,
            summary=summary,
            finished_at=self.clock.now(),
            last_error=str(errors[0].judge.get("error", "")) if errors else "",
        )

    async def _case(self, tenant_id: UUID, run: EvalRunRecord, case: EvalCaseRecord) -> None:
        transcript: list[dict[str, str]] = []
        try:
            tenant = await self.tenants.get_tenant(tenant_id)
            base = self.personas.list_personas(tenant.slug, str(run.summary.get("offer_key", "")))
            persona = next(
                p for p in [*base, *language_variants(base)] if p.key == case.persona_key
            )
            draft = await self.versions.draft(tenant_id)
            if run.alternative_content is not None:
                draft = replace(
                    draft, content=ProfileContent.model_validate(run.alternative_content)
                )
            elif draft.rev != run.draft_rev:
                raise NotAllowed("El borrador cambió desde que se encolaron las evals.")
            if case.target == "published" and run.published_version_id:
                version = await self.versions.get(tenant_id, run.published_version_id)
            else:
                canonical = json.dumps(
                    draft.content.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                version = VersionRecord(
                    uuid4(),
                    tenant_id,
                    0,
                    f"borrador rev {draft.rev}",
                    draft.content,
                    hashlib.sha256(canonical.encode()).hexdigest(),
                    "none",
                    "",
                    UUID(int=0),
                    self.clock.now(),
                )
            offer_key = str(run.summary.get("offer_key", ""))
            if offer_key and offer_key not in {o.key for o in version.content.offers}:
                raise NotAllowed(
                    "La versión comparada no contiene esta oferta; evalúa sin comparar."
                )
            effective = profile_for_offer(version.content, offer_key).model_copy(deep=True)
            # Isolated simulator: evaluate this brief without publishing or opening a vacancy.
            effective.offers = []
            version = replace(version, content=effective)
            repo, decisions, queue = (
                InMemoryConversationRepository(),
                InMemoryDecisionRepository(),
                InMemoryJobQueue(),
            )
            repo.add_tenant(tenant)
            versions = FixedProfileVersions(version, draft)
            assessment = AssessmentService(
                decisions, repo, versions, self.evaluator, queue, self.clock, self.evaluator_model
            )
            evidence_sink = _EvidenceSink(self.sink, repo)
            registry = EvalConnectorRegistry(self.registry, persona, self.clock)
            gateway = CapabilityGateway(
                repo,
                registry,
                evidence_sink,
                self.clock,
                DecisionBookingPolicy(decisions, repo, queue),
                assessment,
            )
            gateway.interviews = InterviewService(
                InMemoryInterviewStore(self.clock),
                repo,
                versions,
                registry,
                decisions,
                queue,
                self.clock,
            )
            gateway.faq = FaqService(self.faq.embeddings, MemoryFaqStore()) if self.faq else None
            if self.faq is not None and any(s.mode == "search" for s in version.content.sources):
                assert gateway.faq is not None
                await gateway.faq.index(
                    RunContext(
                        "conversador",
                        tenant_id,
                        tenant.slug,
                        "tenant",
                        "nobody",
                        None,
                        None,
                        version.id,
                        frozenset(),
                        {},
                        case.id,
                    ),
                    version.content,
                )
            service = ConversationService(
                repo, versions, self.runner_factory(gateway), self.clock, "", signals=self.signals
            )
            _, link = await service.ingest(
                tenant.slug, NewApplication(f"eval-{case.id}", persona.name, "", "", "eval")
            )
            opened = await service.open_session(
                link.rsplit("/", 1)[1], channel="sim", eval_case_id=case.id
            )
            await service.start(tenant_id, opened.session_id)
            ctx = RunContext(
                "simulador",
                tenant_id,
                tenant.slug,
                "tenant",
                "nobody",
                None,
                None,
                None,
                frozenset(),
                {},
                case.id,
            )
            reminders = 0
            policy = version.content.reengagement
            for index in range(self.MAX_CANDIDATE_TURNS):
                messages = await repo.list_messages(tenant_id, opened.session_id)
                session = await repo.get_session(tenant_id, opened.session_id)
                if session.status != "active":
                    break
                text = await self.simulator.next_message(ctx, persona, messages)
                if text is None:
                    break
                if text == SILENCE:
                    # The same platform turn production runs when silence is detected.
                    if not policy.enabled or reminders >= policy.max_attempts:
                        break
                    reminders += 1
                    # Simulated time jumps to when production would actually send it.
                    last = max(m.created_at for m in messages)
                    app = await repo.get_application(tenant_id, session.application_id)
                    at = reminder_due(policy, reminders, last, app.timezone)
                    await service.remind(
                        session,
                        f"recordatorio {reminders} de {policy.max_attempts} por silencio",
                        "reminder",
                        at=at,
                    )
                    continue
                await service.handle_message(tenant_id, opened.session_id, str(index), text)
            messages = await repo.list_messages(tenant_id, opened.session_id)
            transcript = [
                {
                    "from": "agent" if m.direction == "out" else "candidate",
                    "text": m.text,
                    # Reviewers must tell a platform reminder from an ordinary reply.
                    **({"channel": "reminder"} if m.channel == "reminder" else {}),
                }
                for m in messages
            ]
            result = await decisions.get_result(tenant_id, opened.session_id)
            judge = await self.judge.judge(
                replace(ctx, agent="juez"),
                version.content,
                persona,
                messages,
                result,
                evidence_sink.judge_evidence,
            )
            verdict = result.verdict if result else "review"
            score = case_score(judge, version.content.rubric, verdict, persona.expected_verdict)
            await self.evals.finish_case(
                tenant_id,
                case.id,
                transcript,
                judge.model_dump(),
                verdict,
                score,
                "done",
                await self.evals.case_cost(tenant_id, case.id),
            )
        except Exception as exc:
            await self.evals.finish_case(
                tenant_id,
                case.id,
                transcript,
                {"error": str(exc)},
                "",
                None,
                "error",
                await self.evals.case_cost(tenant_id, case.id),
            )
