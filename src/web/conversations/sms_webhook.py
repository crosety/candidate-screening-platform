"""Authenticated monotonic SMS delivery receipts; no body, phone, or token logging."""

import re
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from adapters.channels.twilio import valid_twilio_signature
from screening.domain.errors import NotAllowed
from web.conversations.models import Event, SmsNotification
from web.conversations.sms_links import sms_configuration
from web.conversations.telephone import configuration
from web.tenancy.context import tenant_context

RANK = {
    "queued": 0,
    "sending": 1,
    "accepted": 1,
    "sent": 2,
    "failed": 3,
    "undelivered": 3,
    "delivered": 4,
}


@csrf_exempt
def sms_webhook(request: HttpRequest, tenant_id: UUID, notification_id: UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    if len(request.body) > 24000:
        return HttpResponse(status=413)
    if request.content_type != "application/x-www-form-urlencoded" or request.META.get(
        "QUERY_STRING"
    ):
        return HttpResponse(status=400)
    try:
        config = sms_configuration(tenant_id, active=False)
        account = configuration(tenant_id, active=False)
    except NotAllowed:
        return HttpResponse(status=403)
    params = dict(request.POST.lists())
    if any(len(v) != 1 for v in params.values()) or not valid_twilio_signature(
        config.callback(notification_id),
        params,
        request.headers.get("X-Twilio-Signature", ""),
        account.auth_token.get_secret_value(),
    ):
        return HttpResponse(status=403)
    data = request.POST
    sid, observed = data.get("MessageSid", ""), data.get("MessageStatus", "")
    if (
        data.get("AccountSid") != account.account_sid
        or not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid)
        or observed not in RANK
    ):
        return HttpResponse(status=403)
    with tenant_context(tenant_id):
        row = SmsNotification.objects.select_for_update().filter(pk=notification_id).first()
        if (
            not row
            or not row.attempted_at
            or data.get("To") != row.contact
            or data.get("From") != account.sender
            or row.state == "canceled"
            or (row.provider_id and row.provider_id != sid)
            or SmsNotification.objects.filter(provider_id=sid).exclude(pk=row.pk).exists()
        ):
            return HttpResponse(status=403)
        row.provider_id = sid
        if RANK[observed] > RANK.get(row.state, -1):
            row.state = observed
        row.save(update_fields=["provider_id", "state", "updated_at"])
        Event.objects.get_or_create(
            tenant_id=tenant_id,
            session_id=row.session_id,
            type="sms_link_status",
            payload={"notification_id": str(row.id), "state": row.state},
        )
    return HttpResponse(status=204)
