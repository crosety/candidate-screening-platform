from django.urls import path

from web.api import api
from web.conversations.meta_webhook import whatsapp_webhook
from web.conversations.sms_webhook import sms_webhook
from web.conversations.telephone_webhook import telephone_webhook
from web.conversations.twilio_webhook import twilio_webhook
from web.conversations.voice_api import voice_callback

urlpatterns = [
    path("api/v1/webhooks/twilio-sms/<uuid:tenant_id>/<uuid:notification_id>/status", sms_webhook),
    path(
        "api/v1/webhooks/twilio-voice/<uuid:tenant_id>/<uuid:attempt_id>/answer",
        telephone_webhook,
        {"kind": "answer"},
    ),
    path(
        "api/v1/webhooks/twilio-voice/<uuid:tenant_id>/<uuid:attempt_id>/status",
        telephone_webhook,
        {"kind": "status"},
    ),
    path("api/v1/webhooks/twilio/<uuid:tenant_id>/inbound", twilio_webhook, {"kind": "inbound"}),
    path("api/v1/webhooks/twilio/<uuid:tenant_id>/status", twilio_webhook, {"kind": "status"}),
    path("api/v1/voice/<uuid:tenant_id>/v1/chat/completions", voice_callback),
    path("api/v1/webhooks/whatsapp/<uuid:tenant_id>", whatsapp_webhook),
    path("api/v1/", api.urls),
]
