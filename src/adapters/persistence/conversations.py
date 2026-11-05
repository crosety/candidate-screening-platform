from dataclasses import asdict
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import IntegrityError
from django.utils import timezone

from adapters.persistence.followups import cancel_pending
from screening.application.ports import (
    ApplicationRecord,
    LinkTarget,
    MessageRecord,
    NewApplication,
    SerializedMessage,
    SessionRecord,
    TenantRecord,
)
from screening.domain.errors import Conflict, NotFound
from screening.domain.run_context import RunContext, run_context_from_dict, run_context_to_dict
from screening.domain.signals import TurnSignal
from web.conversations.links import derived_token, digest_token, visible_token
from web.conversations.models import (
    Application,
    CandidateLink,
    Event,
    Message,
    ModelMessage,
    Offer,
    Session,
)
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant
from web.tracing.models import CapabilityCall


def _application(row: Application) -> ApplicationRecord:
    return ApplicationRecord(
        row.id,
        row.tenant_id,
        row.external_id,
        row.candidate_name,
        row.phone,
        row.city,
        row.job_ref,
        row.timezone,
        row.segment,
        row.is_test,
    )


def _session(row: Session) -> SessionRecord:
    return SessionRecord(
        row.id,
        row.tenant_id,
        row.application_id,
        row.profile_version_id,
        row.channel,
        row.status,
        run_context_from_dict(row.run_context),
        row.system_prompt,
        row.created_at,
        row.language,
        row.abandoned_stage,
        row.preferred_language,
        row.fallback_language,
    )


def _message(row: Message) -> MessageRecord:
    return MessageRecord(
        row.id,
        row.direction,
        row.text,
        row.created_at,
        row.channel,
        row.provider_message_id,
        row.delivery_status,
        row.simulated,
        row.client_message_id,
        row.language,
        row.signal,
    )


def _find_session(tenant_id: UUID, session_id: UUID) -> Session:
    try:
        return Session.objects.get(
            tenant_id=tenant_id, pk=session_id, application__erased_at__isnull=True
        )
    except Session.DoesNotExist:
        raise NotFound("Conversación no encontrada") from None


class DjangoConversationRepository:
    @sync_to_async
    def get_tenant(self, tenant_id: UUID) -> TenantRecord:
        with tenant_context(tenant_id):
            try:
                row = Tenant.objects.get(pk=tenant_id)
            except Tenant.DoesNotExist:
                raise NotFound("Cliente no encontrado") from None
            return TenantRecord(row.id, row.slug, row.name)

    @sync_to_async
    def get_tenant_by_slug(self, slug: str) -> TenantRecord:
        try:
            row = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist:
            raise NotFound("Cliente no encontrado") from None
        return TenantRecord(row.id, row.slug, row.name)

    @sync_to_async
    def upsert_application(self, tenant_id: UUID, data: NewApplication) -> ApplicationRecord:
        values = asdict(data)
        values.pop("external_id")
        values["is_simulation"] = data.is_test
        with tenant_context(tenant_id):
            if Application.objects.filter(
                tenant_id=tenant_id, external_id=data.external_id, erased_at__isnull=False
            ).exists():
                raise NotFound("Candidatura retirada")
            offer, _ = Offer.objects.get_or_create(tenant_id=tenant_id, key=data.job_ref)
            values["offer"] = offer
            existing = Application.objects.filter(
                tenant_id=tenant_id, external_id=data.external_id
            ).first()
            if existing and existing.job_ref != data.job_ref:
                raise Conflict("Una candidatura existente no puede cambiar de oferta.")
            row, _ = Application.objects.update_or_create(
                tenant_id=tenant_id, external_id=data.external_id, defaults=values
            )
            return _application(row)

    @sync_to_async
    def get_application(self, tenant_id: UUID, application_id: UUID) -> ApplicationRecord:
        with tenant_context(tenant_id):
            try:
                return _application(
                    Application.objects.get(
                        tenant_id=tenant_id, pk=application_id, erased_at__isnull=True
                    )
                )
            except Application.DoesNotExist:
                raise NotFound("Candidatura no encontrada") from None

    @sync_to_async
    def get_or_create_link(self, tenant_id: UUID, application_id: UUID) -> str:
        with tenant_context(tenant_id):
            try:
                Application.objects.select_for_update().get(
                    tenant_id=tenant_id, pk=application_id, erased_at__isnull=True
                )
            except Application.DoesNotExist:
                raise NotFound("Candidatura no encontrada") from None
            row = CandidateLink.objects.filter(
                tenant_id=tenant_id, application_id=application_id
            ).first()
            if row is None:
                row = CandidateLink(tenant_id=tenant_id, application_id=application_id)
                row.created_at = timezone.now()
                # auto_now_add sets the timestamp during save; derive only after it is fixed.
                row.token = digest_token(str(row.id))
                row.save()
                token = derived_token(row)
                row.token = digest_token(token)
                row.save(update_fields=["token"])
                return token
            current_token = visible_token(row)
            if current_token is None:
                raise Conflict("El enlace anterior necesita renovación explícita por selección.")
            return current_token

    @sync_to_async
    def resolve_link(self, token: str) -> LinkTarget:
        try:
            row = CandidateLink.objects.get(
                token=digest_token(token), revoked_at__isnull=True, expires_at__gt=timezone.now()
            )
        except CandidateLink.DoesNotExist:
            raise NotFound("Enlace no válido") from None
        return LinkTarget(row.tenant_id, row.application_id)

    @sync_to_async
    def latest_session(self, tenant_id: UUID, application_id: UUID) -> SessionRecord | None:
        with tenant_context(tenant_id):
            row = (
                Session.objects.filter(tenant_id=tenant_id, application_id=application_id)
                .order_by("-created_at")
                .first()
            )
            return _session(row) if row else None

    @sync_to_async
    def create_session(
        self,
        tenant_id: UUID,
        session_id: UUID,
        application_id: UUID,
        profile_version_id: UUID,
        channel: str,
        run_context: RunContext,
        system_prompt: str,
    ) -> SessionRecord:
        with tenant_context(tenant_id):
            try:
                Application.objects.select_for_update().get(pk=application_id, tenant_id=tenant_id)
            except Application.DoesNotExist:
                raise NotFound("Candidatura no encontrada") from None
            existing = (
                Session.objects.filter(tenant_id=tenant_id, application_id=application_id)
                .order_by("-created_at")
                .first()
            )
            if existing is not None:
                return _session(existing)
            row = Session.objects.create(
                id=session_id,
                tenant_id=tenant_id,
                application_id=application_id,
                profile_version_id=profile_version_id,
                channel=channel,
                status="active",
                run_context=run_context_to_dict(run_context),
                system_prompt=system_prompt,
            )
            return _session(row)

    @sync_to_async
    def get_session(self, tenant_id: UUID, session_id: UUID) -> SessionRecord:
        with tenant_context(tenant_id):
            return _session(_find_session(tenant_id, session_id))

    @sync_to_async
    def set_session_status(self, tenant_id: UUID, session_id: UUID, status: str) -> None:
        with tenant_context(tenant_id):
            row = _find_session(tenant_id, session_id)
            row.status = status
            row.ended_at = (
                timezone.now() if status in ("handed_off", "completed", "abandoned") else None
            )
            row.save(update_fields=["status", "ended_at"])
            if status != "active":
                cancel_pending(tenant_id, session_id)

    @sync_to_async
    def add_message(
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
    ) -> MessageRecord | None:
        try:
            with tenant_context(tenant_id):
                _find_session(tenant_id, session_id)
                row = Message.objects.create(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    direction=direction,
                    text=text,
                    client_message_id=client_message_id,
                    channel=channel,
                    provider_message_id=provider_message_id,
                    simulated=simulated,
                )
                if direction == "in":
                    cancel_pending(tenant_id, session_id)
                return _message(row)
        except IntegrityError:
            with tenant_context(tenant_id):
                if (
                    client_message_id
                    and Message.objects.filter(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        client_message_id=client_message_id,
                    ).exists()
                ):
                    return None
                if (
                    provider_message_id
                    and Message.objects.filter(
                        tenant_id=tenant_id,
                        channel=channel,
                        provider_message_id=provider_message_id,
                    ).exists()
                ):
                    return None
            raise

    @sync_to_async
    def record_signal(
        self, tenant_id: UUID, session_id: UUID, message_id: UUID, signal: TurnSignal
    ) -> None:
        with tenant_context(tenant_id):
            Message.objects.filter(
                tenant_id=tenant_id, session_id=session_id, id=message_id
            ).update(language=signal.language, signal=signal.stored())
            if signal.language:
                Session.objects.filter(tenant_id=tenant_id, id=session_id).update(
                    language=signal.language
                )

    @sync_to_async
    def set_language_choice(
        self, tenant_id: UUID, session_id: UUID, preferred: str, fallback: str
    ) -> None:
        with tenant_context(tenant_id):
            row = _find_session(tenant_id, session_id)
            row.preferred_language, row.fallback_language = preferred, fallback
            row.save(update_fields=["preferred_language", "fallback_language"])

    @sync_to_async
    def set_abandoned_stage(self, tenant_id: UUID, session_id: UUID, stage: str) -> None:
        with tenant_context(tenant_id):
            Session.objects.filter(tenant_id=tenant_id, id=session_id).update(
                abandoned_stage=stage[:60]
            )

    @sync_to_async
    def list_messages(self, tenant_id: UUID, session_id: UUID) -> list[MessageRecord]:
        with tenant_context(tenant_id):
            _find_session(tenant_id, session_id)
            return [
                _message(row)
                for row in Message.objects.filter(
                    tenant_id=tenant_id, session_id=session_id
                ).order_by("created_at", "id")
            ]

    @sync_to_async
    def count_incoming(self, tenant_id: UUID, session_id: UUID) -> int:
        with tenant_context(tenant_id):
            _find_session(tenant_id, session_id)
            return Message.objects.filter(
                tenant_id=tenant_id, session_id=session_id, direction="in"
            ).count()

    @sync_to_async
    def load_history(self, tenant_id: UUID, session_id: UUID) -> list[SerializedMessage]:
        with tenant_context(tenant_id):
            _find_session(tenant_id, session_id)
            return [
                SerializedMessage(row.role, row.content)
                for row in ModelMessage.objects.filter(
                    tenant_id=tenant_id, session_id=session_id
                ).order_by("seq")
            ]

    @sync_to_async
    def tool_evidence(self, tenant_id: UUID, session_id: UUID) -> list[dict[str, Any]]:
        with tenant_context(tenant_id):
            _find_session(tenant_id, session_id)
            # Only successful domain reads; exclude guard prompts and failed/write calls.
            return [
                dict(row)
                for row in CapabilityCall.objects.filter(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    status="ok",
                    capability__in=["openings.find", "credential.verify", "application.read_mine"],
                )
                .order_by("created_at", "id")
                .values("capability", "request", "response")
            ]

    @sync_to_async
    def append_history(
        self, tenant_id: UUID, session_id: UUID, start_seq: int, messages: list[SerializedMessage]
    ) -> None:
        try:
            with tenant_context(tenant_id):
                _find_session(tenant_id, session_id)
                ModelMessage.objects.bulk_create(
                    [
                        ModelMessage(
                            tenant_id=tenant_id,
                            session_id=session_id,
                            seq=start_seq + index,
                            role=item.role,
                            content=item.content,
                        )
                        for index, item in enumerate(messages)
                    ]
                )
        except IntegrityError as exc:
            raise Conflict("El historial ha cambiado.") from exc

    @sync_to_async
    def add_event(
        self, tenant_id: UUID, session_id: UUID | None, type: str, payload: dict[str, Any]
    ) -> None:
        with tenant_context(tenant_id):
            if session_id is not None:
                session = _find_session(tenant_id, session_id)
                if type == "candidate_snapshot":
                    # Serialize with erasure; an in-flight model must never recreate erased data.
                    app = Application.objects.select_for_update().get(pk=session.application_id)
                    if app.erased_at is not None:
                        return
            Event.objects.create(
                tenant_id=tenant_id, session_id=session_id, type=type, payload=payload
            )
            if type in ("handoff_requested", "auto_handoff") and session_id is not None:
                from web.jobs.enqueue import enqueue

                count = Message.objects.filter(session_id=session_id, direction="in").count()
                if count:
                    enqueue(
                        "candidate_snapshot",
                        {"session_id": str(session_id)},
                        tenant_id,
                        f"snapshot:{session_id}:{count}",
                        max_attempts=1,
                    )
