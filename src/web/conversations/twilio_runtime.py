"""Sandbox orchestration over the existing conversation and human-ownership services."""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from asgiref.sync import sync_to_async
from django.utils import timezone
from ninja.errors import HttpError

from adapters.channels.twilio import TwilioClient, TwilioConfig
from screening.domain.channels import (
    ChannelName,
    DeliveryReceipt,
    OutboundMessage,
    is_opt_out,
)
from screening.domain.profile import ProfileContent
from screening.platform.container import get_container
from web.conversations.apply_api import ApplyInput, create_application, published_offer
from web.conversations.meta_webhook import apply_status
from web.conversations.models import (
    ChannelDelivery,
    ChannelIdentity,
    ChannelInbound,
    Event,
    Message,
    Session,
    TwilioOutbound,
)
from web.conversations.twilio_webhook import configuration
from web.jobs.enqueue import enqueue
from web.profiles.models import Publication
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def queue_text(
    identity: ChannelIdentity, text: str, key: str, message: Message | None = None
) -> None:
    row, created = TwilioOutbound.objects.get_or_create(
        tenant_id=identity.tenant_id,
        key=key,
        defaults={"identity": identity, "text": text, "message": message},
    )
    if created:
        # A new intake step supersedes unsent menus/name prompts, not conversation messages.
        TwilioOutbound.objects.filter(
            identity=identity, message__isnull=True, state="pending", attempted_at__isnull=True
        ).exclude(pk=row.pk).update(state="blocked", reason="intake_updated")
        if message:
            message.delivery_status = "pending"
            message.save(update_fields=["delivery_status"])
        enqueue(
            "twilio_send",
            {"outbound_id": str(row.id)},
            identity.tenant_id,
            "twilio:out:" + str(row.id),
            max_attempts=1,
        )


def queue_human(session: Session, message: Message) -> None:
    if session.channel != "whatsapp":
        return
    identity = ChannelIdentity.objects.filter(
        tenant_id=session.tenant_id, application=session.application, provider="twilio"
    ).first()
    if identity:
        queue_text(identity, message.text, "message:" + str(message.id), message)


MAX_MENU_OFFERS = 10
ENGLISH = {
    "chosen": "You chose {title}. What is your full name?",
    "declined": "OK, I won't start the interview. If you change your mind, write your full name.",
    "name": "Please write your first and last name.",
}
SPANISH = {
    "chosen": "Has elegido {title}. ¿Cómo te llamas? Indica tu nombre y apellidos.",
    "declined": "De acuerdo, no iniciaré la entrevista. Si decides continuar, escribe tu nombre "
    "y apellidos.",
    "name": "Indica tu nombre y apellidos, por escrito.",
}


def copy(state: dict[str, Any], key: str, **values: str) -> str:
    return (ENGLISH if state.get("language") == "en" else SPANISH)[key].format(**values)


def menu(identity: ChannelIdentity, tenant: Tenant) -> str:
    publication = (
        Publication.objects.filter(tenant=tenant, state="active", segment="")
        .select_related("version")
        .order_by("-created_at")
        .first()
    )
    if not publication or publication.percent != 100:
        return "Ahora no hay ofertas disponibles. Inténtalo más tarde."
    profile = ProfileContent.model_validate(publication.version.content)
    # WhatsApp bodies are capped at 1,600 characters; ten titles fit with the notice.
    offers = [o for o in profile.offers if o.status == "open"][:MAX_MENU_OFFERS]
    identity.onboarding = {
        "offers": [o.key for o in offers],
        "version": str(publication.version_id),
    }
    identity.save(update_fields=["onboarding", "updated_at"])
    if not offers:
        return (
            "Ahora no hay ofertas disponibles. Inténtalo más tarde.\nNo open positions right now."
        )
    return (
        f"Hola, soy el asistente virtual de selección de {tenant.name}, una inteligencia "
        f"artificial. Hi, I'm {tenant.name}'s AI recruiting assistant.\n\n"
        + "\n".join(f"{i}. {o.title}" for i, o in enumerate(offers, 1))
        + "\n\nPara empezar, responde ACEPTO y el número (por ejemplo, ACEPTO 1). Al aceptar, "
        f"tus respuestas se usarán en este proceso y podrá revisarlas el equipo de {tenant.name}. "
        "No envíes documentos ni datos sensibles. Escribe BAJA para no recibir más mensajes.\n"
        "To start, reply ACCEPT and the number (e.g. ACCEPT 1). Your answers will be used for "
        f"this application and reviewed by the {tenant.name} team. Reply STOP to opt out."
    )


@sync_to_async
def prepare(tenant_id: UUID, inbound_id: UUID) -> tuple[UUID | None, str, str]:
    """Persist menu/consent before any inference. Called under the per-contact lock."""
    with tenant_context(tenant_id):
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        row = ChannelInbound.objects.select_for_update().get(pk=inbound_id)
        if row.payload.get("processed"):
            return None, "", ""
        contact, text = row.payload["contact"], row.payload["text"]
        identity, _ = ChannelIdentity.objects.select_for_update().get_or_create(
            tenant=tenant, channel="whatsapp", contact=contact, defaults={"provider": "twilio"}
        )
        if identity.provider != "twilio" or identity.simulated:
            raise ValueError("El contacto pertenece a otro transporte")
        at = datetime.fromisoformat(row.payload["received_at"])
        identity.last_inbound_at = max(identity.last_inbound_at or at, at)
        identity.save(update_fields=["last_inbound_at", "updated_at"])
        row.payload["processed"] = True
        row.save(update_fields=["payload"])
        session = (
            Session.objects.filter(application_id=identity.application_id)
            .order_by("-created_at")
            .first()
            if identity.application_id
            else None
        )
        if is_opt_out(text):
            identity.opted_out = True
            identity.save(update_fields=["opted_out", "updated_at"])
            if session:
                from adapters.persistence.followups import cancel_pending

                cancel_pending(tenant_id, session.id)
                Message.objects.get_or_create(
                    tenant=tenant,
                    session=session,
                    client_message_id=hashlib.sha256(row.provider_id.encode()).hexdigest()[:60],
                    defaults={
                        "direction": "in",
                        "channel": "whatsapp",
                        "text": text,
                        "provider_message_id": row.provider_id,
                    },
                )
                if session.status == "active":
                    session.status, session.ended_at = "abandoned", timezone.now()
                    session.save(update_fields=["status", "ended_at"])
                Event.objects.create(
                    tenant=tenant, session=session, type="channel_opt_out", payload={}
                )
            return None, "", ""
        if identity.opted_out:
            return None, "", ""
        if text.upper() == "NUEVA" and session and session.status in {"completed", "abandoned"}:
            identity.application = None
            identity.onboarding = {}
            identity.save(update_fields=["application", "onboarding", "updated_at"])
            session = None
        key = "inbound:" + str(row.id)
        if session:
            if session.status not in {"active", "handed_off"}:
                message = Message.objects.create(
                    tenant=tenant,
                    session=session,
                    direction="in",
                    channel="whatsapp",
                    text=text,
                    provider_message_id=row.provider_id,
                )
                Event.objects.create(
                    tenant=tenant,
                    session=session,
                    type="late_inbound_needs_review",
                    payload={"message_id": str(message.id)},
                )
                return None, "", ""
            if session.status == "active" and text == "[Contenido no compatible]":
                queue_text(
                    identity,
                    "Este canal admite únicamente texto. Escribe tu respuesta, por favor.",
                    key,
                )
                return None, "", ""
            if session.status == "active" and text.casefold().strip(" .!¡") in {
                "llamame",
                "llámame",
            }:
                queue_text(
                    identity,
                    "Las llamadas telefónicas aún no están activadas. "
                    "Podemos continuar esta entrevista por WhatsApp.",
                    key,
                )
                return None, "", ""
            return session.id, text, row.provider_id
        if identity.application_id:
            raise ValueError("La candidatura no tiene conversación")
        state = identity.onboarding
        choice = re.fullmatch(r"(ACEPTO|ACCEPT)\s+(\d{1,2})", text.upper().strip(" .!"))
        if choice and state.get("offers"):
            index = int(choice.group(2)) - 1
            if index < len(state["offers"]):
                offer_key = state["offers"][index]
                try:
                    version, _, offer = published_offer(tenant, offer_key)
                    if str(version.id) != state.get("version"):
                        raise HttpError(409, "Configuración actualizada")
                except HttpError:
                    queue_text(
                        identity, "Las ofertas han cambiado.\n\n" + menu(identity, tenant), key
                    )
                    return None, "", ""
                state.update(
                    selected=offer_key,
                    consent_at=at.isoformat(),
                    consent_sid=row.provider_id,
                    language="en" if choice.group(1) == "ACCEPT" else "es",
                )
                identity.save(update_fields=["onboarding", "updated_at"])
                queue_text(identity, copy(state, "chosen", title=offer.title), key)
                return None, "", ""
        if state.get("selected") and not choice:
            # A refusal is not a name or a withdrawal of all contact consent.
            # Keep the intake pending; only explicit BAJA/STOP opts the contact out.
            if re.match(
                r"^(?:no(?:\s+(?:quiero|deseo|voy|puedo|gracias|thanks))?\b|prefiero\s+no\b"
                r"|i\s+don'?t\s+want\b)",
                text.casefold().lstrip(" ¡¿"),
            ):
                queue_text(identity, copy(state, "declined"), key)
                return None, "", ""
            if (
                not 3 <= len(text) <= 200
                or len(text.split()) < 2
                or text == "[Contenido no compatible]"
            ):
                queue_text(identity, copy(state, "name"), key)
                return None, "", ""
            try:
                version, _, _ = published_offer(tenant, state["selected"])
                if str(version.id) != state.get("version"):
                    raise HttpError(409, "Configuración actualizada")
                # The sandbox must never silently treat a real applicant as synthetic.
                fixture = Event.objects.filter(
                    type="workspace_example_loaded",
                    payload__offer_keys__contains=[state["selected"]],
                ).exists()
                if not fixture:
                    raise HttpError(409, "El Sandbox solo admite el perfil de prueba")
                result = create_application(
                    tenant,
                    state["selected"],
                    ApplyInput(
                        request_id=uuid5(
                            NAMESPACE_URL, str(identity.id) + ":" + state["consent_sid"]
                        ),
                        name=text,
                        phone=contact,
                        consent=True,
                    ),
                    "whatsapp",
                )
            except HttpError:
                queue_text(
                    identity, "La oferta se ha actualizado.\n\n" + menu(identity, tenant), key
                )
                return None, "", ""
            session = Session.objects.get(pk=result["session_id"])
            if state.get("language"):
                # The greeting turn has no candidate text yet; start from the chosen language.
                session.language = state["language"]
                session.save(update_fields=["language"])
            identity.application_id = session.application_id
            identity.save(update_fields=["application", "updated_at"])
            Event.objects.create(
                tenant=tenant,
                session=session,
                type="whatsapp_consent",
                payload={
                    "at": state["consent_at"],
                    "provider_message_id": state["consent_sid"],
                    "notice_version": "2026-10-27",
                    "provider": "twilio",
                },
            )
            return session.id, "", row.provider_id
        queue_text(identity, menu(identity, tenant), key)
        return None, "", ""


@sync_to_async
def save_reply(tenant: UUID, sid: UUID, message_id: UUID) -> None:
    with tenant_context(tenant):
        session = Session.objects.get(pk=sid)
        identity = ChannelIdentity.objects.get(
            application_id=session.application_id, provider="twilio"
        )
        message = Message.objects.get(pk=message_id)
        if message.direction == "out":
            queue_text(identity, message.text, "message:" + str(message.id), message)


@sync_to_async
def contact_for(tenant: UUID, inbound: UUID) -> str:
    with tenant_context(tenant):
        return str(ChannelInbound.objects.get(pk=inbound).payload["contact"])


async def receive(tenant: UUID, inbound: UUID) -> None:
    config = configuration(tenant)
    contact = await contact_for(tenant, inbound)
    if contact not in config.participants:
        raise ValueError("Participante no autorizado")
    c = get_container()
    # Separate from the session lock, shared between worker processes.
    async with c.conversations.turn_lock.hold(tenant, uuid5(NAMESPACE_URL, "twilio:" + contact)):
        sid, text, provider_id = await prepare(tenant, inbound)
        if sid is None:
            return
        if text:
            result = await c.conversations.handle_message(
                tenant,
                sid,
                hashlib.sha256(provider_id.encode()).hexdigest()[:60],
                text,
                channel="whatsapp",
                provider_message_id=provider_id,
            )
        else:
            result = await c.conversations.start(tenant, sid, "whatsapp")
        if result.message and result.message.direction == "out":
            await save_reply(tenant, sid, result.message.id)


def fail(row: TwilioOutbound, state: str, reason: str) -> None:
    row.state, row.reason = state, reason
    row.save(update_fields=["state", "reason"])
    if row.message_id:
        assert row.message is not None
        Message.objects.filter(pk=row.message_id).update(
            delivery_status="pending" if state == "unknown" else "failed"
        )
        Event.objects.create(
            tenant_id=row.tenant_id,
            session_id=row.message.session_id,
            type="channel_delivery_needs_review",
            payload={"message_id": str(row.message_id), "reason": reason},
        )


@sync_to_async
def claim_send(tenant: UUID, outbound: UUID, config: TwilioConfig) -> tuple[str, str] | None:
    with tenant_context(tenant):
        # A single sandbox/account has one global send cadence, even across worker processes.
        Tenant.objects.select_for_update().get(pk=tenant)
        row = (
            TwilioOutbound.objects.select_for_update(of=("self",))
            .select_related("identity", "message__session")
            .get(pk=outbound)
        )
        if row.state != "pending" or row.attempted_at:
            return None
        if not row.text.strip() or len(row.text) > 1600:
            fail(row, "blocked", "invalid_message_length")
            return None
        identity = row.identity
        now = timezone.now()
        if (
            identity.provider != "twilio"
            or identity.opted_out
            or identity.contact not in config.participants
        ):
            fail(row, "blocked", "contact_not_authorized")
            return None
        if (
            not identity.last_inbound_at
            or now - identity.last_inbound_at >= timedelta(hours=24)
            or identity.last_inbound_at > now
        ):
            fail(row, "blocked", "window_expired")
            return None
        if row.message_id:
            assert row.message is not None
            session = row.message.session
            if session.application_id != identity.application_id:
                fail(row, "blocked", "application_changed")
                return None
            human = row.message.channel == "human"
            claimed_later = Event.objects.filter(
                session=session, type="human_claim", created_at__gte=row.message.created_at
            ).exists()
            if not human and (session.human_owner_id or claimed_later):
                fail(row, "blocked", "human_takeover")
                return None
        attempts = TwilioOutbound.objects.filter(
            tenant_id=tenant, attempted_at__gte=now - timedelta(days=1)
        )
        if attempts.count() >= config.max_outbound_per_day:
            fail(row, "blocked", "daily_budget")
            return None
        previous = attempts.order_by("-attempted_at").first()
        earlier = TwilioOutbound.objects.filter(
            identity=identity,
            created_at__lt=row.created_at,
            state="pending",
            attempted_at__isnull=True,
        ).exists()
        due = now + timedelta(seconds=3)
        rate_limited = (
            previous
            and previous.attempted_at
            and (due := previous.attempted_at + timedelta(seconds=3)) > now
        )
        if earlier or rate_limited:
            due = max(due, now + timedelta(seconds=3))
            enqueue(
                "twilio_send",
                {"outbound_id": str(row.id)},
                tenant,
                f"twilio:defer:{row.id}:{due.isoformat()}",
                due,
                max_attempts=1,
            )
            return None
        row.state, row.attempted_at = "dispatching", now
        row.save(update_fields=["state", "attempted_at"])
        return identity.contact, row.text


@sync_to_async
def record_send(tenant: UUID, outbound: UUID, provider_id: str) -> None:
    with tenant_context(tenant):
        row = TwilioOutbound.objects.select_for_update().get(pk=outbound)
        if not provider_id:
            fail(row, "unknown", "delivery_unconfirmed_no_retry")
            return
        row.provider_id, row.state = provider_id, "pending"
        row.save(update_fields=["provider_id", "state"])
        if row.message_id:
            Message.objects.filter(pk=row.message_id).update(
                provider_message_id=provider_id, delivery_status="pending"
            )
        apply_status(tenant, provider_id, "pending")
        row.state = ChannelDelivery.objects.get(provider_id=provider_id).status
        row.save(update_fields=["state"])


@sync_to_async
def send_session(tenant: UUID, outbound: UUID) -> tuple[UUID, str]:
    with tenant_context(tenant):
        row = TwilioOutbound.objects.select_related("message", "identity").get(pk=outbound)
        return (row.message.session_id if row.message else row.identity_id, row.identity.contact)


async def dispatch(tenant: UUID, outbound: UUID) -> None:
    config = configuration(tenant)
    c = get_container()
    sid, contact = await send_session(tenant, outbound)
    # Serialize opt-out with sends, and HR ownership with final check + POST.
    async with (
        c.conversations.turn_lock.hold(tenant, uuid5(NAMESPACE_URL, "twilio:" + contact)),
        c.conversations.turn_lock.hold(tenant, sid),
    ):
        payload = await claim_send(tenant, outbound, config)
        if payload is None:
            return
        try:
            provider_id = await TwilioClient(config).send(*payload)
        except Exception:
            await record_send(tenant, outbound, "")
            raise ValueError("Entrega Twilio no confirmada; revisión manual, sin reenvío") from None
        await record_send(tenant, outbound, provider_id)


class TwilioQueueAdapter:
    """Proactive WhatsApp through the Sandbox queue, so its window, allowlist and budget apply."""

    channel: ChannelName = "whatsapp"

    @sync_to_async
    def send(self, message: OutboundMessage) -> DeliveryReceipt:
        with tenant_context(message.tenant_id):
            identity = ChannelIdentity.objects.get(
                tenant_id=message.tenant_id,
                channel="whatsapp",
                contact=message.contact,
                provider="twilio",
            )
            row = Message.objects.filter(
                tenant_id=message.tenant_id, pk=message.idempotency_key
            ).first()
            queue_text(identity, message.text, f"message:{message.idempotency_key}", row)
        # Queued, not delivered: the Twilio status callback settles the final state.
        return DeliveryReceipt("", "pending", timezone.now())
