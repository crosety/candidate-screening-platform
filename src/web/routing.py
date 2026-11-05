from django.urls import path, re_path

from web.conversations.consumers import ChatConsumer
from web.conversations.eleven_direct import ElevenDirectConsumer
from web.conversations.gpt_live import GptLiveConsumer
from web.core.consumers import HealthConsumer

websocket_urlpatterns = [
    path("ws/health/", HealthConsumer.as_asgi()),
    re_path(r"^ws/chat/(?P<session_id>[0-9a-f-]{36})/$", ChatConsumer.as_asgi()),
    re_path(r"^ws/voice/live/(?P<session_id>[0-9a-f-]{36})/$", GptLiveConsumer.as_asgi()),
    re_path(r"^ws/voice/direct/(?P<session_id>[0-9a-f-]{36})/$", ElevenDirectConsumer.as_asgi()),
]
