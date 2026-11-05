"""Consent-scoped invitations and post-call links, with a durable spending ceiling."""

from pathlib import Path
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from adapters.channels.twilio_sms import SEGMENT_PRICE, SmsConfig, TwilioSmsClient, segments
from screening.application.ports import SessionRecord
from screening.domain.errors import NotAllowed
from web.conversations.links import visible_token
from web.conversations.models import (
    CandidateLink,
    Event,
    Session,
    SmsNotification,
    TelephoneAttempt,
)
from web.conversations.telephone import configuration
from web.jobs.enqueue import enqueue
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def sms_configuration(tenant: UUID, *, active: bool = True) -> SmsConfig:
    try:
        config = SmsConfig.model_validate_json(Path(settings.TWILIO_SMS_CONFIG_FILE).read_text())
    except (OSError, ValueError):
        raise NotAllowed("Avisos SMS no configurados") from None
    if config.tenant_id != tenant or (active and not config.active):
        raise NotAllowed("Avisos SMS no habilitados")
    return config


def sms_text(
    config: SmsConfig, link: CandidateLink, purpose: str, session_status: str = "active"
) -> str:
    token = visible_token(link)
    if not token:
        raise NotAllowed("El enlace ya no es válido; no se envía")
    url = f"{config.candidate_base_url}/c/{token}"
    if session_status != "active":
        continuation = (
            "Puedes continuar por chat con el equipo."
            if session_status == "handed_off"
            else "Consulta el historial y los siguientes pasos."
        )
        return f"Tu conversacion de seleccion: {url} {continuation} No respondas a este SMS."
    if purpose == "after_call":
        return (
            "Gracias por hablar con nosotros. Revisa la conversacion y continua por chat "
            f"o voz en {url} No respondas a este SMS."
        )
    return (
        f"Tu entrevista con el asistente de IA: {url} Puedes escribir, hablar por voz "
        "o pedir que te llamemos. No respondas a este SMS."
    )


def continuation_enabled(session: SessionRecord) -> bool:
    with tenant_context(session.tenant_id):
        return (
            SmsNotification.objects.filter(
                session_id=session.id,
                purpose="invitation",
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
                link__revoked_at__isnull=True,
                link__expires_at__gt=timezone.now(),
            )
            .exclude(state__in=["failed", "canceled"])
            .exists()
        )


def reserve_sms(
    tenant: UUID, sid: UUID, link_id: UUID, contact: str, key: str, purpose: str = "invitation"
) -> SmsNotification:
    config = sms_configuration(tenant)
    account = configuration(tenant, active=False)
    if purpose not in {"invitation", "after_call"} or contact not in account.participants:
        raise NotAllowed("Destino o propósito SMS no autorizado")
    with tenant_context(tenant):
        Tenant.objects.select_for_update().get(pk=tenant)
        session = Session.objects.select_related("application").get(pk=sid)
        if session.application.erased_at or not session.application.is_test:
            raise NotAllowed("No se pueden enviar avisos para esta candidatura")
        link = CandidateLink.objects.filter(
            pk=link_id, tenant_id=tenant, application_id=session.application_id
        ).first()
        if not link:
            raise NotAllowed("Enlace ajeno a la candidatura")
        previous = SmsNotification.objects.filter(key=key).first()
        if previous:
            if (
                previous.session_id != sid
                or previous.contact != contact
                or previous.link_id != link_id
            ):
                raise NotAllowed("Identificador de aviso utilizado")
            return previous
        if (
            purpose == "invitation"
            and SmsNotification.objects.filter(
                session_id=sid, purpose=purpose, revoked_at__isnull=True
            ).exists()
        ):
            raise NotAllowed("Ya se solicitó el enlace; no se envía otro automáticamente")
        count = segments(sms_text(config, link, purpose, session.status))
        total = SmsNotification.objects.aggregate(total=Sum("segment_count"))["total"] or 0
        if (
            count > 3
            or total + count > config.max_segments
            or (total + count) * SEGMENT_PRICE > 3_000_000
        ):
            raise NotAllowed("Se ha alcanzado el presupuesto autorizado de SMS")
        row = SmsNotification.objects.create(
            tenant_id=tenant,
            session=session,
            link=link,
            key=key,
            purpose=purpose,
            contact=contact,
            consent_at=timezone.now(),
            expires_at=min(config.expires_at, link.expires_at),
            segment_count=count,
            reserved_usd_micros=count * SEGMENT_PRICE,
        )
        Event.objects.create(
            tenant_id=tenant,
            session=session,
            type="sms_link_requested",
            payload={
                "notification_id": str(row.id),
                "purpose": purpose,
                "consent": "sms_link_and_after_call_v1",
            },
        )
        enqueue(
            "sms_link_send",
            {"notification_id": str(row.id)},
            tenant,
            "sms:" + str(row.id),
            max_attempts=1,
        )
        return row


def after_call(row: TelephoneAttempt) -> None:
    """Called inside the signed terminal callback transaction; replay is harmless."""
    if row.state != "completed" or not row.stream_used_at:
        return
    consent = (
        SmsNotification.objects.filter(
            session_id=row.session_id,
            contact=row.contact,
            purpose="invitation",
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
            link__token=row.link_digest,
        )
        .order_by("-created_at")
        .first()
    )
    if not consent:
        return
    try:
        reserve_sms(
            row.tenant_id,
            row.session_id,
            consent.link_id,
            row.contact,
            "after_call:" + str(row.id),
            "after_call",
        )
    except NotAllowed:
        Event.objects.get_or_create(
            tenant_id=row.tenant_id,
            session_id=row.session_id,
            type="sms_after_call_blocked",
            payload={"attempt_id": str(row.id)},
        )


def revoke_sms(tenant: UUID, sid: UUID) -> None:
    with tenant_context(tenant):
        Tenant.objects.select_for_update().get(pk=tenant)
        SmsNotification.objects.filter(session_id=sid, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        SmsNotification.objects.filter(session_id=sid, attempted_at__isnull=True).update(
            state="canceled", reason="consent_withdrawn"
        )


@sync_to_async
def claim(tenant: UUID, pk: UUID) -> tuple[SmsNotification, str] | None:
    with tenant_context(tenant):
        Tenant.objects.select_for_update().get(pk=tenant)
        row = (
            SmsNotification.objects.select_for_update()
            .select_related("link", "session__application")
            .get(pk=pk)
        )
        if row.attempted_at or row.state != "queued":
            return None
        try:
            config = sms_configuration(tenant)
            account = configuration(tenant, active=False)
        except NotAllowed:
            row.state, row.reason = "canceled", "configuration_expired"
            row.save(update_fields=["state", "reason", "updated_at"])
            return None
        if (
            row.revoked_at
            or row.expires_at <= timezone.now()
            or row.contact not in account.participants
            or row.session.application.erased_at
            or not visible_token(row.link)
        ):
            row.state, row.reason = "canceled", "authorization_changed"
            row.save(update_fields=["state", "reason", "updated_at"])
            return None
        text = sms_text(config, row.link, row.purpose, row.session.status)
        if segments(text) > row.segment_count:
            row.state, row.reason = "canceled", "configuration_changed"
            row.save(update_fields=["state", "reason", "updated_at"])
            return None
        row.attempted_at, row.state = timezone.now(), "sending"
        row.save(update_fields=["attempted_at", "state", "updated_at"])
        return row, text


@sync_to_async
def result(tenant: UUID, pk: UUID, state: str, sid: str = "") -> None:
    with tenant_context(tenant):
        row = SmsNotification.objects.select_for_update().get(pk=pk)
        if sid:
            if row.provider_id and row.provider_id != sid:
                raise NotAllowed("Identidad SMS contradictoria")
            row.provider_id = sid
        if row.state in {"queued", "sending", "unknown"}:
            row.state = state
        row.save(update_fields=["provider_id", "state", "updated_at"])


async def dispatch(tenant: UUID, pk: UUID) -> None:
    claimed = await claim(tenant, pk)
    if not claimed:
        return
    row, text = claimed
    try:
        config = sms_configuration(tenant)
        account = configuration(tenant, active=False)
        sid = await TwilioSmsClient(account, config).send(row.contact, text, row.id)
    except NotAllowed:
        await result(tenant, pk, "failed")
    except Exception:
        # A send may have reached the operator. Never retry an ambiguous outcome.
        await result(tenant, pk, "unknown")
    else:
        await result(tenant, pk, "accepted", sid)


def public_state(row: SmsNotification) -> dict[str, object]:
    return {
        "id": str(row.id),
        "purpose": row.purpose,
        "state": row.state,
        "created_at": row.created_at.isoformat(),
        "revoked": row.revoked_at is not None,
        "segments": row.segment_count,
    }
