"""Create-only local fixture over the ACTIVE published version; it never publishes anything.

The profile reaches production through the eval gate like any other change. This command
only adds fictional candidates to offers that version already has open.
"""

import hashlib
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone
from langchain_core.messages import AIMessage, HumanMessage, message_to_dict
from pydantic import BaseModel, ConfigDict, Field, model_validator

from adapters.persistence.profiles import DjangoProfileRepository
from screening.application.prompts import build_system_prompt
from screening.domain.profile import ProfileContent, profile_for_offer
from screening.domain.result import FieldValue
from screening.domain.run_context import RunContext, run_context_to_dict
from web.conversations.links import derived_token, digest_token
from web.conversations.models import (
    Application,
    CandidateLink,
    Event,
    InterviewBooking,
    Message,
    ModelMessage,
    Offer,
    Session,
)
from web.decisions.models import Decision, Result
from web.simulated.models import AtsRecord, CalendarSlot, Site
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent, Membership, Tenant


class ExampleMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["in", "out"]
    text: str = Field(min_length=1, max_length=2000)


class ExampleInterview(BaseModel):
    """The interview the conversation already booked: next such weekday and local time."""

    model_config = ConfigDict(extra="forbid")
    site_id: str = Field(min_length=1, max_length=60)
    weekday: int = Field(ge=0, le=6)  # 0 = Monday, as in datetime.weekday()
    time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class ExampleCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9_-]{1,25}$")
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(pattern=r"^\+(?:34|52)000[0-9]{6}$")
    offer: str
    city: str = Field(default="València", max_length=120)
    timezone: str = "Europe/Madrid"
    language: str = Field(default="", pattern=r"^(?:[a-z]{2})?$")
    abandoned_stage: str = Field(default="", max_length=60)
    status: Literal["active", "handed_off", "completed", "abandoned", "awaiting"]
    hours_ago: int = Field(ge=1, le=720)
    messages: list[ExampleMessage] = Field(default_factory=list, max_length=60)
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    summary: str = ""
    handoff_reason: str = ""
    verdict: Literal["qualified", "not_qualified", "review", "pool"] | None = None
    decision: Literal["proposed", "applied"] = "proposed"
    interview: ExampleInterview | None = None


class WorkspaceExample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9_-]{1,25}$")
    offers: list[str] = Field(min_length=1, max_length=20)
    candidates: list[ExampleCandidate] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def coherent(self) -> "WorkspaceExample":
        if len({c.key for c in self.candidates}) != len(self.candidates):
            raise ValueError("Referencias de candidatos repetidas")
        for candidate in self.candidates:
            if candidate.offer not in self.offers:
                raise ValueError("Oferta no declarada en el ejemplo")
            if candidate.status == "awaiting" and (
                candidate.messages or candidate.verdict or candidate.fields
            ):
                raise ValueError("Una candidatura sin iniciar no puede tener entrevista")
            if candidate.status != "awaiting" and not candidate.messages:
                raise ValueError("Falta la conversación")
            if candidate.status == "handed_off" and not candidate.handoff_reason:
                raise ValueError("Falta el motivo de derivación")
            if candidate.verdict and candidate.status != "completed":
                raise ValueError("Las valoraciones finales necesitan conversación completada")
            if candidate.abandoned_stage and candidate.status != "abandoned":
                raise ValueError("Solo una candidatura abandonada tiene etapa de abandono")
            if candidate.interview and candidate.verdict != "qualified":
                raise ValueError("Solo una candidatura apta tiene entrevista reservada")
        return self


def check_against(example: WorkspaceExample, profile: ProfileContent) -> None:
    """The fixture must fit the version that is already published; nothing is adapted."""
    if not any(
        c.transport == "native" and c.settings.get("driver") == "sim_ats"
        for c in profile.connections
    ):
        raise ValueError("Los ejemplos solo admiten un ATS simulado")
    if any(c.interview for c in example.candidates) and not any(
        c.transport == "native" and c.settings.get("driver") == "sim_calendar"
        for c in profile.connections
    ):
        raise ValueError("Las entrevistas del ejemplo necesitan una agenda simulada")
    open_offers = {o.key for o in profile.offers if o.status == "open"}
    if not set(example.offers) <= open_offers:
        raise ValueError("Las ofertas del ejemplo deben estar abiertas en la versión activa")
    for candidate in example.candidates:
        definitions = {f.key: f for f in profile_for_offer(profile, candidate.offer).result_schema}
        if candidate.abandoned_stage and candidate.abandoned_stage not in {
            *definitions,
            "summary",
            "interview",
            "unknown",
        }:
            raise ValueError("Etapa de abandono ajena al esquema")
        for key, field in candidate.fields.items():
            if key not in definitions:
                raise ValueError("Campo ajeno a la oferta")
            if field.value is None:
                continue
            if not field.evidence or not any(
                field.evidence in m.text for m in candidate.messages if m.direction == "in"
            ):
                raise ValueError("Falta una cita literal del candidato")
            kind, value = definitions[key].value_type, field.value
            if (
                (kind == "boolean" and not isinstance(value, bool))
                or (
                    kind == "number"
                    and (isinstance(value, bool) or not isinstance(value, (int, float)))
                )
                or (kind in ("text", "enum", "date") and not isinstance(value, str))
                or (kind == "enum" and value not in definitions[key].choices)
            ):
                raise ValueError("Tipo de respuesta incompatible con el campo")


class Command(BaseCommand):
    help = "Carga candidaturas de ejemplo aisladas sin modificar las existentes."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("fixture", type=Path)
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--recruiter", default="reclutadora@orbio.local")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            raw = options["fixture"].read_bytes()
            example = WorkspaceExample.model_validate_json(raw)
        except (OSError, ValueError) as error:
            raise CommandError(str(error)) from error
        tenant = Tenant.objects.get(slug=options["tenant"])
        if options["apply"] and urlparse(settings.CHAT_BASE_URL).hostname not in {
            "localhost",
            "127.0.0.1",
        }:
            raise CommandError("La carga solo se permite en un entorno localhost")
        repo = DjangoProfileRepository()
        publication = repo.active_publication(tenant.id)
        if publication is None:
            raise CommandError("Publica antes una versión con estas ofertas")
        version = repo.get_version(tenant.id, publication.version_id)
        member = Membership.objects.filter(
            tenant=tenant, user__email=options["actor"], role__in=["admin", "fde"]
        ).first()
        if member is None:
            raise CommandError("El actor debe ser FDE de este cliente")
        recruiter = Membership.objects.filter(
            tenant=tenant, user__email=options["recruiter"], role="recruiter"
        ).first()
        if recruiter is None:
            raise CommandError("Indica un reclutador existente de este cliente")
        digest = hashlib.sha256(raw).hexdigest()
        with tenant_context(tenant.id):
            Tenant.objects.select_for_update().get(pk=tenant.pk)
            # Events are append-only: an additive upgrade appends a newer load record.
            loaded = (
                Event.objects.filter(type="workspace_example_loaded", payload__key=example.key)
                .order_by("-created_at")
                .first()
            )
            if loaded:
                if loaded.payload["sha256"] != digest:
                    raise CommandError(
                        "El ejemplo ya existe con otro contenido; utiliza otra referencia"
                    )
                self.stdout.write("Ya cargado: se conservan todas las interacciones y decisiones.")
                return
            if Application.objects.filter(
                external_id__in=[f"{example.key}:{c.key}" for c in example.candidates]
            ).exists():
                raise CommandError("Hay referencias en uso. No se sobrescribe ningún registro.")
            try:
                check_against(example, version.content)
            except ValueError as error:
                raise CommandError(str(error)) from error
            self.stdout.write(
                f"Validado contra la versión {version.version}: {len(example.offers)} ofertas y "
                f"{len(example.candidates)} candidaturas de ejemplo."
            )
            if not options["apply"]:
                self.stdout.write("Sin cambios. Añade --apply para cargar este ejemplo.")
                return
            if (
                "*" not in recruiter.allowed_segments
                and example.key not in recruiter.allowed_segments
            ):
                recruiter.allowed_segments = [*recruiter.allowed_segments, example.key]
                recruiter.save(update_fields=["allowed_segments"])
                AuditEvent.objects.create(
                    tenant=tenant,
                    actor_id=member.user_id,
                    action="workspace.local_access",
                    payload={"membership_id": str(recruiter.id), "segment": example.key},
                )
            records = [
                self.load_candidate(
                    tenant, version.content, example, candidate, version.id, member.user_id
                )
                for candidate in example.candidates
            ]
            Event.objects.create(
                tenant=tenant,
                type="workspace_example_loaded",
                payload={
                    "key": example.key,
                    "sha256": digest,
                    "source": "fictional_fixture",
                    "version_id": str(version.id),
                    # New applications to these offers are classified as part of this fixture.
                    "offer_keys": example.offers,
                    "records": records,
                },
            )
            self.stdout.write(
                f"Cargado en {tenant.name} sobre la versión publicada {version.version}; "
                "no se publica nada. Sin envíos ni inferencias."
            )

    def load_candidate(
        self,
        tenant: Tenant,
        profile: ProfileContent,
        example: WorkspaceExample,
        candidate: ExampleCandidate,
        version_id: UUID,
        actor_id: UUID,
    ) -> dict[str, str]:
        offer, _ = Offer.objects.get_or_create(tenant=tenant, key=candidate.offer)
        app = Application.objects.create(
            tenant=tenant,
            external_id=f"{example.key}:{candidate.key}",
            candidate_name=candidate.name,
            phone=candidate.phone,
            city=candidate.city,
            timezone=candidate.timezone,
            job_ref=candidate.offer,
            offer=offer,
            segment=example.key,
            is_test=True,
            is_simulation=False,
        )
        start = timezone.now() - timedelta(hours=candidate.hours_ago)
        Application.objects.filter(pk=app.pk).update(created_at=start - timedelta(minutes=20))
        AtsRecord.objects.create(
            tenant=tenant,
            external_id=app.external_id,
            candidate_name=app.candidate_name,
            phone=app.phone,
            city=app.city,
            job_ref=app.job_ref,
            stage="Pendiente" if candidate.status == "awaiting" else "Entrevista",
            fields={},
            notes=[],
        )
        record = {"key": candidate.key, "application_id": str(app.pk)}
        if candidate.status == "awaiting":
            return record
        effective = profile_for_offer(profile, candidate.offer)
        sid = uuid4()
        ctx = RunContext(
            agent="conversador",
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            acts_for="tenant",
            talks_to="candidate",
            session_id=sid,
            application_id=app.id,
            profile_version_id=version_id,
            granted=frozenset(effective.grants.conversador),
            autonomy=effective.autonomy.model_dump(),
            offer_key=candidate.offer,
        )
        session = Session.objects.create(
            id=sid,
            tenant=tenant,
            application=app,
            profile_version_id=version_id,
            channel="web",
            status=candidate.status,
            run_context=run_context_to_dict(ctx),
            system_prompt=build_system_prompt(effective, tenant.name, "web"),
            language=candidate.language,
            abandoned_stage=candidate.abandoned_stage,
        )
        end = start + timedelta(minutes=len(candidate.messages) * 2)
        Session.objects.filter(pk=sid).update(
            created_at=start,
            ended_at=end if candidate.status in ("completed", "abandoned") else None,
        )
        for index, message in enumerate(candidate.messages):
            row = Message.objects.create(
                tenant=tenant,
                session=session,
                direction=message.direction,
                text=message.text,
                channel="web",
                simulated=True,
            )
            Message.objects.filter(pk=row.pk).update(
                created_at=start + timedelta(minutes=(index + 1) * 2)
            )
            ModelMessage.objects.create(
                tenant=tenant,
                session=session,
                seq=index,
                role="user" if message.direction == "in" else "assistant",
                content=message_to_dict(
                    HumanMessage(message.text)
                    if message.direction == "in"
                    else AIMessage(message.text)
                ),
            )
        fields = {key: field.model_dump() for key, field in candidate.fields.items()}
        if candidate.verdict:
            result = Result.objects.create(
                tenant=tenant,
                session=session,
                verdict=candidate.verdict,
                fields=fields,
                summary=candidate.summary,
                evaluator_model="fictional_fixture",
                flags=[],
            )
            Result.objects.filter(pk=result.pk).update(created_at=end)
            kind = {
                "qualified": "qualify",
                "not_qualified": "reject",
                "review": "review",
                "pool": "pool",
            }[candidate.verdict]
            Decision.objects.create(
                tenant=tenant,
                session=session,
                decision_type=kind,
                status=candidate.decision,
                autonomy_level=1,
                decided_by="user" if candidate.decision == "applied" else "agent",
                user_id=actor_id if candidate.decision == "applied" else None,
                reason=candidate.summary,
                applied_at=end if candidate.decision == "applied" else None,
            )
        elif candidate.summary:
            Event.objects.bulk_create(
                [
                    Event(
                        tenant=tenant,
                        session=session,
                        created_at=end,
                        type="candidate_snapshot",
                        payload={
                            "fields": fields,
                            "summary": candidate.summary,
                            "message_count": len(candidate.messages),
                            "source": "fictional_fixture",
                        },
                    )
                ]
            )
        if candidate.status == "abandoned":
            Event.objects.bulk_create(
                [
                    Event(
                        tenant=tenant,
                        session=session,
                        created_at=end,
                        type="followups_exhausted",
                        payload={
                            "stage": candidate.abandoned_stage or "unknown",
                            "reason": "silence",
                            "source": "fictional_fixture",
                        },
                    )
                ]
            )
        if candidate.handoff_reason:
            Event.objects.bulk_create(
                [
                    Event(
                        tenant=tenant,
                        session=session,
                        created_at=end,
                        type="handoff_requested",
                        payload={"reason": candidate.handoff_reason, "source": "fictional_fixture"},
                    )
                ]
            )
        if candidate.interview:
            record["booking_id"] = str(
                book_interview(tenant, profile, candidate, app, session, end).pk
            )
        link = CandidateLink.objects.create(
            tenant=tenant, application_id=app.id, token=digest_token(str(uuid4()))
        )
        link.token = digest_token(derived_token(link))
        link.save(update_fields=["token"])
        record["session_id"] = str(sid)
        return record


def book_interview(
    tenant: Tenant,
    profile: ProfileContent,
    candidate: ExampleCandidate,
    app: Application,
    session: Session,
    end: datetime,
) -> InterviewBooking:
    """Book the slot the conversation agreed, as the calendar tool would; no reminder is sent."""
    interview = candidate.interview
    assert interview is not None
    connection = next(
        c
        for c in profile.connections
        if c.transport == "native" and c.settings.get("driver") == "sim_calendar"
    )
    site = Site.objects.filter(tenant=tenant, external_id=interview.site_id).first()
    if site is None:
        raise CommandError(f"Local desconocido en la entrevista de {candidate.key}")
    zone = ZoneInfo(site.timezone)
    hour, minute = (int(part) for part in interview.time.split(":"))
    after = end.astimezone(zone)
    starts_at = next(
        moment
        for offset in range(8)
        if (day := after.date() + timedelta(days=offset)).weekday() == interview.weekday
        and (moment := datetime.combine(day, time(hour, minute), zone)) > after
    )
    slot, _ = CalendarSlot.objects.get_or_create(tenant=tenant, site=site, starts_at=starts_at)
    if slot.booked_application_external_id not in ("", app.external_id):
        raise CommandError(f"El hueco de {candidate.key} ya está reservado")
    slot.booked_application_external_id = app.external_id
    slot.save(update_fields=["booked_application_external_id"])
    return InterviewBooking.objects.create(
        tenant=tenant,
        session=session,
        application=app,
        connection_id=connection.id,
        slot_ref=str(slot.id),
        site_ref=site.external_id,
        site_name=site.name,
        site_address=site.address,
        site_timezone=site.timezone,
        candidate_timezone=candidate.timezone,
        starts_at=starts_at,
        # Distinct from "none" (too close to remind): the fixture simply schedules no job.
        reminder_status="not_scheduled",
    )
