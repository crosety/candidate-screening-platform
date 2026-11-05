"""Authenticated, durable Twilio callbacks. Never run an agent inside a webhook."""

import re
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from adapters.channels.twilio import TwilioConfig, valid_twilio_signature
from web.conversations.meta_webhook import apply_status
from web.conversations.models import ChannelDelivery, ChannelInbound, TwilioOutbound
from web.jobs.enqueue import enqueue
from web.tenancy.context import tenant_context


def configuration(tenant_id: UUID) -> TwilioConfig:
    try:
        config = TwilioConfig.model_validate_json(Path(settings.TWILIO_CONFIG_FILE).read_text())
    except (OSError, ValueError):
        # Validation errors may contain raw config values; never persist them in job errors.
        raise ValueError("Configuración privada de Twilio ausente o inválida") from None
    if config.tenant_id != tenant_id or not config.active:
        raise ValueError("Sandbox no habilitado para este cliente")
    return config


@csrf_exempt
def twilio_webhook(request: HttpRequest, tenant_id: UUID, kind: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    if len(request.body) > 24_000:
        return HttpResponse(status=413)
    if request.content_type != "application/x-www-form-urlencoded" or request.META.get(
        "QUERY_STRING"
    ):
        return HttpResponse(status=400)
    try:
        config = configuration(tenant_id)
    except (OSError, ValueError):
        return HttpResponse(status=403)
    params = dict(request.POST.lists())
    if any(len(v) != 1 for v in params.values()) or not valid_twilio_signature(
        config.callback(kind),
        params,
        request.headers.get("X-Twilio-Signature", ""),
        config.auth_token.get_secret_value(),
    ):
        return HttpResponse(status=403)
    data = request.POST
    sid = data.get("MessageSid", "")
    if (
        data.get("AccountSid") != config.account_sid
        or re.fullmatch(r"SM[0-9a-fA-F]{32}", sid) is None
    ):
        return HttpResponse(status=403)
    with tenant_context(tenant_id):
        if kind == "status":
            mapping = {
                "queued": "pending",
                "accepted": "pending",
                "sending": "pending",
                "sent": "sent",
                "delivered": "delivered",
                "read": "read",
                "failed": "failed",
                "undelivered": "failed",
            }
            status = mapping.get(data.get("MessageStatus", ""))
            if status:
                apply_status(tenant_id, sid, status)
                observed = ChannelDelivery.objects.get(provider_id=sid).status
                TwilioOutbound.objects.filter(provider_id=sid).update(state=observed)
        elif kind == "inbound":
            contact = data.get("From", "").removeprefix("whatsapp:")
            if (
                data.get("From") != "whatsapp:" + contact
                or data.get("To") != "whatsapp:" + config.sender
                or contact not in config.participants
            ):
                return HttpResponse(status=403)
            text = data.get("Body", "").strip()
            if data.get("NumMedia", "0") != "0" or not text:
                text = "[Contenido no compatible]"
            if len(text) > 4000:
                return HttpResponse(status=413)
            row, _ = ChannelInbound.objects.get_or_create(
                tenant_id=tenant_id,
                provider_id="twilio:" + sid,
                defaults={
                    "payload": {
                        "contact": contact,
                        "text": text,
                        "received_at": timezone.now().isoformat(),
                    }
                },
            )
            enqueue(
                "twilio_inbound",
                {"inbound_id": str(row.id)},
                tenant_id,
                "twilio:in:" + str(tenant_id) + ":" + sid,
                max_attempts=1,
            )
        else:
            return HttpResponse(status=404)
    # Empty TwiML acknowledges receipt, not delivery. The worker sends separately.
    return HttpResponse("<Response/>", content_type="application/xml")
