import logging
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache
from django.utils import timezone

from screening.application.conversation import TurnOutcome
from screening.application.ports import MessageRecord
from screening.domain.errors import DomainError, NotAllowed
from screening.platform.container import get_container
from web.conversations.models import CandidateLink
from web.core.security import consume_socket_ticket, limited

logger = logging.getLogger(__name__)


@sync_to_async
def valid_socket_link(digest: str, tenant_id: UUID, application_id: UUID) -> bool:
    return CandidateLink.objects.filter(
        token=digest,
        tenant_id=tenant_id,
        application_id=application_id,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()


def message_payload(message: MessageRecord) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "direction": message.direction,
        "text": message.text,
        "created_at": message.created_at.isoformat(),
        "channel": message.channel,
        "client_message_id": message.client_message_id,
    }


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        self.service = get_container().conversations
        try:
            self.session_id = UUID(self.scope["url_route"]["kwargs"]["session_id"])
            ticket = parse_qs(self.scope["query_string"].decode()).get("ticket", [""])[0]
            payload = await sync_to_async(consume_socket_ticket)(ticket, self.session_id)
            if payload is None:
                raise NotAllowed("Ticket no válido")
            session = await self.service.repo.get_session(
                UUID(payload["tenant_id"]), self.session_id
            )
            self.link_digest = str(payload["token_digest"])
            self.application_id = session.application_id
            if not await valid_socket_link(
                self.link_digest, session.tenant_id, session.application_id
            ):
                raise NotAllowed("Enlace revocado")
        except (DomainError, ValueError, UnicodeError):
            await self.accept()
            await self.close(code=4403)
            return
        self.tenant_id, self.status = session.tenant_id, session.status
        self.group_name = f"chat_{self.session_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        messages = await self.service.repo.list_messages(self.tenant_id, self.session_id)
        await self.send_json(
            {
                "type": "history",
                "messages": [message_payload(m) for m in messages],
                "status": self.status,
            }
        )
        if (
            self.status == "active"
            and not messages
            and not await sync_to_async(cache.get)("live-call:" + str(self.session_id))
            and not await self.service.repo.load_history(self.tenant_id, self.session_id)
        ):
            await self._broadcast({"type": "typing", "on": True})
            try:
                await self._outcome(await self.service.start(self.tenant_id, self.session_id))
            except Exception as exc:
                await self._failure(exc)
            finally:
                await self._broadcast({"type": "typing", "on": False})

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if not await valid_socket_link(self.link_digest, self.tenant_id, self.application_id):
            await self.close(code=4403)
            return
        if await sync_to_async(limited)("ws_message", str(self.session_id), 50):
            await self.close(code=4429)
            return
        if not isinstance(content, dict):
            await self.send_json(
                {"type": "error", "code": "invalid_message", "message": "Mensaje no válido."}
            )
            return
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})
            return
        if content.get("type") != "message":
            await self.send_json(
                {"type": "error", "code": "unknown_type", "message": "Tipo de mensaje desconocido."}
            )
            return
        if await sync_to_async(cache.get)("live-call:" + str(self.session_id)):
            await self.send_json(
                {
                    "type": "error",
                    "code": "voice_active",
                    "message": "Sal de voz antes de escribir.",
                }
            )
            return
        text, client_id = content.get("text"), content.get("client_message_id")
        try:
            if (
                not isinstance(text, str)
                or not 1 <= len(text.strip()) <= 2000
                or not isinstance(client_id, str)
            ):
                raise ValueError("invalid message")
            UUID(client_id)
        except ValueError:
            await self.send_json(
                {
                    "type": "error",
                    "code": "invalid_message",
                    "message": "Escribe entre 1 y 2.000 caracteres con un identificador válido.",
                }
            )
            return
        await self._broadcast({"type": "typing", "on": True})
        try:
            await self._outcome(
                await self.service.handle_message(
                    self.tenant_id, self.session_id, client_id, text.strip()
                )
            )
        except NotAllowed as exc:
            code = "session_closed" if str(exc) == "session_closed" else "not_allowed"
            await self.send_json(
                {
                    "type": "error",
                    "code": code,
                    "message": "La conversación ya ha terminado."
                    if code == "session_closed"
                    else "No se puede enviar este mensaje.",
                }
            )
        except Exception as exc:
            await self._failure(exc)
        finally:
            await self._broadcast({"type": "typing", "on": False})

    async def _outcome(self, result: TurnOutcome) -> None:
        if result.message is not None:
            await self._broadcast({"type": "message", "message": message_payload(result.message)})
        if result.status != self.status:
            self.status = result.status
            await self._broadcast({"type": "status", "status": result.status})
        if result.action == "telephone_request":
            await self._broadcast({"type": "telephone_request"})

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        # Send locally now so typing is visible during the awaited model call.
        await self.send_json(payload)
        await self.channel_layer.group_send(
            self.group_name, {"type": "chat.event", "payload": payload, "origin": self.channel_name}
        )

    async def chat_event(self, event: dict[str, Any]) -> None:
        if event.get("origin") != self.channel_name:
            await self.send_json(event["payload"])

    async def _failure(self, exc: Exception) -> None:
        logger.error("Fallo del turno en sesión %s (%s)", self.session_id, type(exc).__name__)
        await self.send_json(
            {
                "type": "error",
                "code": "turn_failed",
                "message": "No se ha podido responder. Vuelve a intentarlo.",
            }
        )
