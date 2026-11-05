"""Signed call identity binding and monotonic delivery state, without inference."""

from uuid import UUID
from xml.sax.saxutils import quoteattr

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from adapters.channels.twilio import valid_twilio_signature
from adapters.channels.twilio_voice import CALL_SID, SPANISH_MOBILE
from screening.domain.errors import NotAllowed
from web.conversations.models import Event, TelephoneAttempt
from web.conversations.telephone import FINAL, configuration, live_session
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


@csrf_exempt
def telephone_webhook(
    request: HttpRequest, tenant_id: UUID, attempt_id: UUID, kind: str
) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    if len(request.body) > 24000:
        return HttpResponse(status=413)
    if request.content_type != "application/x-www-form-urlencoded" or request.META.get(
        "QUERY_STRING"
    ):
        return HttpResponse(status=400)
    try:
        config = configuration(tenant_id, active=kind != "status")
    except NotAllowed:
        return HttpResponse(status=403)
    params = dict(request.POST.lists())
    if (
        kind not in {"answer", "status"}
        or any(len(v) != 1 for v in params.values())
        or not valid_twilio_signature(
            config.callback(attempt_id, kind),
            params,
            request.headers.get("X-Twilio-Signature", ""),
            config.auth_token.get_secret_value(),
        )
    ):
        return HttpResponse(status=403)
    data = request.POST
    sid = data.get("CallSid", "")
    if data.get("AccountSid") != config.account_sid or not CALL_SID.fullmatch(sid):
        return HttpResponse(status=403)
    with tenant_context(tenant_id):
        # Use the same lock order as the dispatch/reservation paths and post-call SMS.
        Tenant.objects.select_for_update().get(pk=tenant_id)
        row = (
            TelephoneAttempt.objects.select_for_update()
            .select_related("session__application")
            .filter(pk=attempt_id)
            .first()
        )
        if (
            not row
            or not row.attempted_at
            or data.get("To") != row.contact
            or data.get("From") != config.sender
            or (row.provider_id and row.provider_id != sid)
            or TelephoneAttempt.objects.filter(provider_id=sid).exclude(pk=row.pk).exists()
        ):
            return HttpResponse(status=403)
        row.provider_id = sid
        if kind == "answer":
            if (
                row.state in FINAL
                or row.stream_used_at
                or row.expires_at <= timezone.now()
                or not SPANISH_MOBILE.fullmatch(row.contact)
                or not live_session(row.session, row.link_digest)
                or row.session.profile_version_id != row.profile_version_id
            ):
                return HttpResponse(
                    "<Response><Hangup/></Response>", content_type="application/xml"
                )
            row.state = "in-progress"
            row.save(update_fields=["provider_id", "state", "updated_at"])
            # Disconnecting the stream always falls through to Hangup, never another route.
            xml = (
                f"<Response><Connect><Stream url={quoteattr(config.callback(row.id, 'media'))}/>"
                "</Connect><Hangup/></Response>"
            )
            return HttpResponse(xml, content_type="application/xml")
        observed = data.get("CallStatus", "")
        if (
            (observed in FINAL and row.state not in FINAL)
            or (observed == "in-progress" and row.state not in FINAL)
            or (observed == "ringing" and row.state in {"dialing", "unknown"})
        ):
            row.state = observed
        duration = data.get("CallDuration", "")
        if duration.isdigit() and int(duration) <= 3600:
            row.duration_seconds = int(duration)
        row.save(update_fields=["provider_id", "state", "duration_seconds", "updated_at"])
        Event.objects.get_or_create(
            tenant_id=tenant_id,
            session_id=row.session_id,
            type="telephone_status",
            payload={"attempt_id": str(row.id), "state": row.state},
        )
        if row.state in FINAL and cache.get("live-call:" + str(row.session_id)) == str(row.id):
            cache.delete("live-call:" + str(row.session_id))
        from web.conversations.sms_links import after_call

        after_call(row)
    return HttpResponse("<Response/>", content_type="application/xml")
