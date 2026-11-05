import hashlib
import hmac
import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from adapters.channels.meta import MetaSender, normalize_meta, valid_signature
from adapters.persistence.profiles import DjangoProfileRepository
from adapters.secrets.env import EnvSecretStore
from screening.domain.errors import DomainError, NotFound
from web.conversations.models import ChannelDelivery, ChannelInbound, Event, Message
from web.jobs.enqueue import enqueue
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def schedule_first_contact(tenant_id: UUID, application_id: UUID, phone: str) -> None:
    from django.conf import settings

    if not settings.META_SEND_ENABLED or not phone:
        return
    repo = DjangoProfileRepository()
    publication = repo.active_publication(tenant_id)
    if publication is None:
        return
    profile = repo.get_version(tenant_id, publication.version_id).content
    channel = profile.channel_settings.whatsapp
    if "whatsapp" in profile.channels and channel and channel.first_contact == "template":
        enqueue(
            "channel_first_contact",
            {"application_id": str(application_id)},
            tenant_id,
            f"first-contact:{tenant_id}:{application_id}",
            max_attempts=1,
        )


def sender_config(tenant_id: UUID) -> MetaSender:
    tenant = Tenant.objects.filter(pk=tenant_id).first()
    if tenant is None:
        raise NotFound("Cliente no encontrado")
    repo = DjangoProfileRepository()
    publication = repo.active_publication(tenant_id)
    if publication is None:
        raise NotFound("No hay un perfil publicado")
    profile = repo.get_version(tenant_id, publication.version_id).content
    if "whatsapp" not in profile.channels:
        raise NotFound("WhatsApp no está habilitado en el perfil publicado")
    settings = profile.channel_settings.whatsapp
    if settings is None or settings.sender_ref is None:
        raise NotFound("Meta no está configurado para este cliente")
    if not settings.sender_ref.startswith(f"secret://{tenant.slug}/"):
        raise NotFound("La referencia del emisor no corresponde al cliente")
    return MetaSender.model_validate_json(EnvSecretStore().resolve(settings.sender_ref))


def apply_status(tenant_id: UUID, provider_id: str, status: str) -> None:
    if not Tenant.objects.filter(pk=tenant_id).exists():
        return
    ranks = {"": -1, "pending": 0, "sent": 1, "delivered": 2, "read": 3}
    with tenant_context(tenant_id):
        signal, _ = ChannelDelivery.objects.select_for_update().get_or_create(
            tenant_id=tenant_id, provider_id=provider_id, defaults={"status": status}
        )
        if signal.status not in ("read", "failed") and (
            status == "failed" or ranks.get(status, -1) > ranks.get(signal.status, -1)
        ):
            signal.status = status
            signal.save(update_fields=["status"])
        status = signal.status
        row = (
            Message.objects.select_for_update()
            .filter(
                tenant_id=tenant_id,
                channel__in=["whatsapp", "human"],
                provider_message_id=provider_id,
                direction="out",
                simulated=False,
            )
            .first()
        )
        if row is None or row.delivery_status in ("read", "failed"):
            return
        if status == "failed" or ranks.get(status, -1) > ranks.get(row.delivery_status, -1):
            row.delivery_status = status
            row.save(update_fields=["delivery_status"])
            if status == "failed":
                Event.objects.create(
                    tenant_id=tenant_id,
                    session_id=row.session_id,
                    type="channel_delivery_needs_review",
                    payload={"message_id": str(row.id)},
                )


@csrf_exempt
def whatsapp_webhook(request: HttpRequest, tenant_id: UUID) -> HttpResponse:
    try:
        config = sender_config(tenant_id)
    except (DomainError, ValueError):
        return HttpResponse(status=403)
    if request.method == "GET":
        valid = request.GET.get("hub.mode") == "subscribe" and hmac.compare_digest(
            request.GET.get("hub.verify_token", "").encode(),
            config.verify_token.get_secret_value().encode(),
        )
        response = HttpResponse(
            request.GET.get("hub.challenge", "") if valid else "",
            status=200 if valid else 403,
            content_type="text/plain; charset=utf-8",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
    if request.method != "POST":
        return HttpResponse(status=405)
    body = request.body
    if len(body) > 256_000:
        return HttpResponse(status=413)
    if not valid_signature(
        body, request.headers.get("X-Hub-Signature-256", ""), config.app_secret.get_secret_value()
    ):
        return HttpResponse(status=403)
    try:
        incoming, statuses = normalize_meta(json.loads(body), tenant_id, config.phone_number_id)
        if len(incoming) + len(statuses) > 100:
            return HttpResponse(status=413)
        with transaction.atomic(), tenant_context(tenant_id):
            for item in incoming:
                payload: dict[str, Any] = asdict(item)
                payload["tenant_id"] = str(item.tenant_id)
                payload["received_at"] = item.received_at.isoformat()
                key = hashlib.sha256(item.provider_message_id.encode()).hexdigest()
                stored, _ = ChannelInbound.objects.get_or_create(
                    tenant_id=tenant_id,
                    provider_id=item.provider_message_id,
                    defaults={"payload": payload},
                )
                enqueue(
                    "channel_inbound",
                    {"inbound_id": str(stored.id)},
                    tenant_id,
                    f"meta:{tenant_id}:{key}",
                    max_attempts=1,
                )
            for row in statuses:
                apply_status(tenant_id, row["id"], row["status"])
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, DomainError):
        return JsonResponse({"detail": "Webhook no válido"}, status=400)
    return JsonResponse({"received": True})
