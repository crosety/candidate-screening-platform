"""Durable, bounded telephone admission and single-attempt dispatch."""

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from adapters.channels.twilio_voice import (
    SPANISH_MOBILE,
    CallOutcomeUnknown,
    TwilioVoiceClient,
    TwilioVoiceConfig,
)
from screening.domain.errors import NotAllowed
from screening.domain.reengagement import PLATFORM_QUIET
from screening.platform.container import get_container
from web.conversations.models import Application, CandidateLink, Event, Session, TelephoneAttempt
from web.jobs.enqueue import enqueue
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

FINAL = {"completed", "busy", "failed", "no-answer", "canceled"}
MAX_ATTEMPTS = 10
RESERVE = 2_000_000


def within_call_hours(candidate_timezone: str) -> bool:
    try:
        hour = timezone.now().astimezone(ZoneInfo(candidate_timezone)).hour
    except (ValueError, ZoneInfoNotFoundError):
        return False
    return PLATFORM_QUIET[1] <= hour < PLATFORM_QUIET[0]


def voice_ready(tenant: UUID, session_id: UUID) -> bool:
    from web.conversations.eleven_direct import enabled

    session = async_to_sync(get_container().conversations.repo.get_session)(tenant, session_id)
    return settings.JEV_MODE != "off" and async_to_sync(enabled)(session, telephone=True)


def configuration(tenant: UUID, *, active: bool = True) -> TwilioVoiceConfig:
    try:
        config = TwilioVoiceConfig.model_validate_json(
            Path(settings.TWILIO_VOICE_CONFIG_FILE).read_text()
        )
    except (OSError, ValueError):
        raise NotAllowed("Telefonía no configurada") from None
    if config.tenant_id != tenant or (active and not config.active):
        raise NotAllowed("Las llamadas no están disponibles ahora")
    return config


def live_session(session: Session, digest: str) -> bool:
    return (
        session.status == "active"
        and session.human_owner_id is None
        and session.application.erased_at is None
        and CandidateLink.objects.filter(
            tenant_id=session.tenant_id,
            application_id=session.application_id,
            token=digest,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).exists()
    )


def call_capacity(
    tenant: UUID, config: TwilioVoiceConfig, *, application_id: UUID | None = None
) -> bool:
    if not config.active or config.tenant_id != tenant:
        return False
    with tenant_context(tenant):
        if application_id is not None:
            app = Application.objects.get(pk=application_id)
            if not within_call_hours(app.timezone):
                return False
        calls = TelephoneAttempt.objects.all()
        if calls.count() >= MAX_ATTEMPTS or (
            (calls.aggregate(total=Sum("reserved_usd_micros"))["total"] or 0) + RESERVE > 20_000_000
        ):
            return False
        # The destination is not known until consent is submitted. A depleted
        # mobile must not hide the service from every other candidate.
        return application_id is None or (
            calls.filter(session__application_id=application_id).count()
            < config.max_attempts_per_mobile
        )


def reserve(
    tenant: UUID, session_id: UUID, digest: str, request_id: UUID, contact: str
) -> TelephoneAttempt:
    config = configuration(tenant)
    if not voice_ready(tenant, session_id):
        raise NotAllowed("Las llamadas no están disponibles ahora. Puedes continuar por escrito.")
    with tenant_context(tenant):
        # One lock serializes all quota reservations for this configured account/tenant.
        Tenant.objects.select_for_update().get(pk=tenant)
        session = Session.objects.select_related("application").get(pk=session_id)
        if not live_session(session, digest):
            raise NotAllowed("No se puede solicitar una llamada desde esta conversación")
        if not within_call_hours(session.application.timezone):
            raise NotAllowed("Las llamadas están disponibles de 08:00 a 22:00 en tu horario local.")
        if not SPANISH_MOBILE.fullmatch(contact):
            raise NotAllowed("Indica un móvil español válido con prefijo +34.")
        previous = TelephoneAttempt.objects.filter(request_id=request_id).first()
        if previous:
            if (
                previous.session_id != session_id
                or previous.contact != contact
                or previous.link_digest != digest
            ):
                raise NotAllowed("La solicitud ya corresponde a otro intento")
            return previous
        calls = TelephoneAttempt.objects.all()
        fingerprint = hashlib.sha256(contact.encode()).hexdigest()
        if (
            calls.count() >= MAX_ATTEMPTS
            or (calls.aggregate(total=Sum("reserved_usd_micros"))["total"] or 0) + RESERVE
            > 20_000_000
        ):
            raise NotAllowed("Se ha agotado el cupo autorizado de llamadas")
        if calls.filter(contact_digest=fingerprint).count() >= config.max_attempts_per_mobile:
            count = {1: "un intento", 2: "dos intentos", 3: "tres intentos"}.get(
                config.max_attempts_per_mobile, f"{config.max_attempts_per_mobile} intentos"
            )
            raise NotAllowed(f"Se ha alcanzado el límite autorizado de {count} por móvil")
        if (
            calls.filter(session__application_id=session.application_id).count()
            >= config.max_attempts_per_mobile
        ):
            raise NotAllowed("Se ha alcanzado el límite autorizado por candidatura")
        if calls.filter(session_id=session_id).exclude(state__in=FINAL).exists():
            raise NotAllowed("Ya hay una solicitud pendiente; no se vuelve a marcar")
        row = TelephoneAttempt.objects.create(
            tenant_id=tenant,
            session=session,
            profile_version_id=session.profile_version_id,
            request_id=request_id,
            link_digest=digest,
            contact=contact,
            contact_digest=fingerprint,
            consent_at=timezone.now(),
            expires_at=min(config.expires_at, timezone.now() + timedelta(minutes=10)),
            reserved_usd_micros=RESERVE,
        )
        Event.objects.create(
            tenant_id=tenant,
            session=session,
            type="telephone_requested",
            payload={
                "attempt_id": str(row.id),
                "consent": "voice_callback_v1",
                "reserved_usd_micros": RESERVE,
            },
        )
        enqueue(
            "telephone_dial",
            {"attempt_id": str(row.id)},
            tenant,
            "telephone:" + str(row.id),
            max_attempts=1,
        )
        return row


@sync_to_async
def claim(tenant: UUID, attempt: UUID) -> TelephoneAttempt | None:
    configuration(tenant)
    with tenant_context(tenant):
        row = (
            TelephoneAttempt.objects.select_for_update()
            .select_related("session__application")
            .get(pk=attempt)
        )
        if row.attempted_at or row.state != "queued":
            return None
        if (
            row.expires_at <= timezone.now()
            or not within_call_hours(row.session.application.timezone)
            or not SPANISH_MOBILE.fullmatch(row.contact)
            or row.session.profile_version_id != row.profile_version_id
            or not live_session(row.session, row.link_digest)
        ):
            row.state, row.reason = "canceled", "authorization_changed"
            row.save(update_fields=["state", "reason", "updated_at"])
            return None
        if not cache.add("live-call:" + str(row.session_id), str(row.id), 420):
            row.state, row.reason = "canceled", "other_voice_session"
            row.save(update_fields=["state", "reason", "updated_at"])
            return None
        row.attempted_at, row.state = timezone.now(), "dialing"
        row.save(update_fields=["attempted_at", "state", "updated_at"])
        return row


@sync_to_async
def result(tenant: UUID, attempt: UUID, state: str, sid: str = "", reason: str = "") -> None:
    with tenant_context(tenant):
        row = TelephoneAttempt.objects.select_for_update().get(pk=attempt)
        if sid:
            if row.provider_id and row.provider_id != sid:
                raise NotAllowed("Identidad de llamada contradictoria")
            row.provider_id = sid
        # A callback can arrive before the POST response. Never regress it.
        if row.state in {"queued", "dialing", "unknown"}:
            row.state, row.reason = state, reason
        row.save(update_fields=["provider_id", "state", "reason", "updated_at"])
        Event.objects.create(
            tenant_id=tenant,
            session_id=row.session_id,
            type="telephone_dispatch",
            payload={"attempt_id": str(row.id), "state": row.state, "reason": reason},
        )
        if row.state in FINAL and cache.get("live-call:" + str(row.session_id)) == str(row.id):
            cache.delete("live-call:" + str(row.session_id))


async def dispatch(tenant: UUID, attempt: UUID) -> None:
    # Check voice policy before consuming the durable dispatch attempt.
    from web.conversations.eleven_direct import enabled, signed_url

    @sync_to_async
    def session_id() -> UUID | None:
        with tenant_context(tenant):
            row = TelephoneAttempt.objects.get(pk=attempt)
            # A retry must not reclassify an ambiguous, already-dispatched call
            # as a preflight failure, nor contact the provider again.
            return row.session_id if row.state == "queued" and not row.attempted_at else None

    session_pk = await session_id()
    if session_pk is None:
        return
    try:
        config = configuration(tenant)
        session = await get_container().conversations.repo.get_session(tenant, session_pk)
        if not await enabled(session, telephone=True) or settings.JEV_MODE == "off":
            raise NotAllowed("La voz o el guardián no están preparados")
        await signed_url(session)
        client = TwilioVoiceClient(config)
        await client.preflight()
        row = await claim(tenant, attempt)
    except Exception:
        await result(tenant, attempt, "failed", reason="preflight_failed")
        return
    if row is None:
        return
    try:
        deadline = (
            timezone.now()
            .astimezone(ZoneInfo(row.session.application.timezone))
            .replace(hour=PLATFORM_QUIET[0], minute=0, second=0, microsecond=0)
        )
        sid = await client.dial(row.contact, row.id, not_after=deadline)
    except CallOutcomeUnknown:
        await result(tenant, attempt, "unknown", reason="provider_outcome_unknown")
    except NotAllowed:
        await result(tenant, attempt, "failed", reason="provider_rejected")
    except Exception:
        await result(tenant, attempt, "unknown", reason="dispatch_interrupted")
    else:
        await result(tenant, attempt, "ringing", sid)


def public_state(row: TelephoneAttempt) -> dict[str, str]:
    return {"id": str(row.id), "state": row.state, "reason": row.reason}
