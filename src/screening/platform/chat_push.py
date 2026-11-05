"""Deliver a server-side message to any chat tab the candidate has open."""

from uuid import UUID

from screening.application.ports import MessageRecord


async def push_to_chat(tenant_id: UUID, session_id: UUID, message: MessageRecord) -> None:
    from channels.layers import get_channel_layer

    from web.conversations.consumers import message_payload

    layer = get_channel_layer()
    if layer is not None:
        await layer.group_send(
            f"chat_{session_id}",
            {
                "type": "chat.event",
                "payload": {"type": "message", "message": message_payload(message)},
            },
        )
