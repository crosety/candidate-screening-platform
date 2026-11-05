"""Platform notices inside the candidate's own web conversation (no external send)."""

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from screening.application.ports import ConversationRepository, MessageRecord, SessionRecord

logger = logging.getLogger(__name__)
Push = Callable[[UUID, UUID, MessageRecord], Awaitable[None]]


class WebChatNotifier:
    """Stores the notice in the transcript, then pushes it to any open chat tab.

    Same outbound path as web follow-ups: transcript message plus the chat push. The message
    is idempotent by key (``client_message_id``), so a retried job never posts it twice. The
    live push is best effort: the stored message is what the candidate sees on their link.
    """

    def __init__(self, repo: ConversationRepository, push: Push | None = None) -> None:
        self.repo, self.push = repo, push

    async def notify(self, session: SessionRecord, text: str, key: str) -> bool:
        message = await self.repo.add_message(session.tenant_id, session.id, "out", text, key)
        if message is None:
            return True  # Already posted by an earlier attempt.
        if self.push is not None:
            try:
                await self.push(session.tenant_id, session.id, message)
            except Exception as exc:  # The stored notice remains; only the live push failed.
                logger.warning("Aviso de agenda sin envío en vivo (%s)", type(exc).__name__)
        return True
