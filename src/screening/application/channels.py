import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from screening.application.conversation import ConversationService
from screening.application.ports import ChannelAdapter, Clock, MessageRecord
from screening.domain.channels import (
    ChannelTemplate,
    DeliveryReceipt,
    NormalizedInbound,
    OutboundMessage,
    is_opt_out,
    whatsapp_delivery,
)
from screening.domain.errors import NotAllowed


@dataclass(frozen=True)
class ChannelIdentityRecord:
    tenant_id: UUID
    contact: str
    application_id: UUID | None
    opted_out: bool
    last_inbound_at: datetime | None
    simulated: bool
    clock_offset_seconds: int


class ChannelStore(Protocol):
    async def identity(
        self, tenant_id: UUID, contact: str, simulated: bool = False
    ) -> ChannelIdentityRecord: ...
    async def bind(
        self, tenant_id: UUID, contact: str, application_id: UUID, simulated: bool = False
    ) -> ChannelIdentityRecord: ...
    async def inbound(self, tenant_id: UUID, contact: str, at: datetime) -> None: ...
    async def opt_out(self, tenant_id: UUID, contact: str) -> None: ...
    async def advance(self, tenant_id: UUID, contact: str, seconds: int) -> None: ...
    async def delivered(
        self, tenant_id: UUID, message_id: UUID, receipt: DeliveryReceipt
    ) -> None: ...


@dataclass(frozen=True)
class ChannelOutcome:
    status: str
    session_id: UUID | None = None
    reply: str | None = None
    delivery: DeliveryReceipt | None = None


class ChannelService:
    def __init__(
        self,
        conversations: ConversationService,
        store: ChannelStore,
        adapter: ChannelAdapter,
        clock: Clock,
    ) -> None:
        self.conversations, self.store, self.adapter, self.clock = (
            conversations,
            store,
            adapter,
            clock,
        )

    def now(self, identity: ChannelIdentityRecord) -> datetime:
        return self.clock.now() + timedelta(seconds=identity.clock_offset_seconds)

    async def receive(
        self, incoming: NormalizedInbound, *, simulated: bool = False
    ) -> ChannelOutcome:
        if incoming.channel != "whatsapp":
            raise NotAllowed("Canal no disponible en este adaptador.")
        identity = await self.store.identity(incoming.tenant_id, incoming.contact, simulated)
        if is_opt_out(incoming.text):
            await self.store.opt_out(incoming.tenant_id, incoming.contact)
            if identity.application_id is not None:
                session = await self.conversations.repo.latest_session(
                    incoming.tenant_id, identity.application_id
                )
                if session is not None:
                    await self.conversations.repo.add_message(
                        incoming.tenant_id,
                        session.id,
                        "in",
                        incoming.text,
                        channel="whatsapp",
                        provider_message_id=incoming.provider_message_id,
                        simulated=simulated,
                    )
                    await self.conversations.stop(incoming.tenant_id, session.id)
            return ChannelOutcome("opted_out")
        if identity.opted_out:
            return ChannelOutcome("opted_out")
        if identity.application_id is None and incoming.candidate_code:
            target = await self.conversations.repo.resolve_link(incoming.candidate_code)
            if target.tenant_id != incoming.tenant_id:
                raise NotAllowed("Código no válido para este cliente.")
            identity = await self.store.bind(
                incoming.tenant_id, incoming.contact, target.application_id, simulated
            )
        if identity.application_id is None:
            return ChannelOutcome(
                "unknown_contact",
                reply="Para continuar, abre el enlace de candidatura que te ha enviado el equipo.",
            )
        if incoming.received_at > self.now(identity):
            raise NotAllowed("La fecha recibida no puede estar en el futuro.")
        await self.store.inbound(incoming.tenant_id, incoming.contact, incoming.received_at)
        token = await self.conversations.repo.get_or_create_link(
            incoming.tenant_id, identity.application_id
        )
        opened = await self.conversations.open_session(token, "whatsapp")
        client_id = hashlib.sha256(incoming.provider_message_id.encode()).hexdigest()[:60]
        session = await self.conversations.repo.get_session(incoming.tenant_id, opened.session_id)
        if session.status != "active":
            saved = await self.conversations.repo.add_message(
                incoming.tenant_id,
                session.id,
                "in",
                incoming.text,
                client_message_id=client_id,
                channel="whatsapp",
                provider_message_id=incoming.provider_message_id,
                simulated=simulated,
            )
            if saved is not None:
                await self.conversations.repo.add_event(
                    incoming.tenant_id,
                    session.id,
                    "late_inbound_needs_review",
                    {"message_id": str(saved.id)},
                )
            return ChannelOutcome("awaiting_human", session.id)
        if incoming.text == "[Contenido no compatible]":
            return await self.proactive(
                incoming.tenant_id,
                incoming.contact,
                "Por favor, envía tu respuesta por escrito. Este canal no admite ese contenido.",
                simulated=simulated,
            )
        result = await self.conversations.handle_message(
            incoming.tenant_id,
            opened.session_id,
            client_id,
            incoming.text,
            channel="whatsapp",
            provider_message_id=incoming.provider_message_id,
            simulated=simulated,
        )
        if result.message is None:
            return ChannelOutcome("duplicate", opened.session_id)
        receipt = await self._send(identity, opened.session_id, result.message, None)
        return ChannelOutcome(result.status, opened.session_id, result.message.text, receipt)

    async def proactive(
        self,
        tenant_id: UUID,
        contact: str,
        text: str,
        template: ChannelTemplate | None = None,
        *,
        simulated: bool = False,
    ) -> ChannelOutcome:
        identity = await self.store.identity(tenant_id, contact, simulated)
        if identity.application_id is None:
            return ChannelOutcome("unknown_contact")
        app = await self.conversations.repo.get_application(tenant_id, identity.application_id)
        decision = whatsapp_delivery(
            now=self.now(identity),
            last_inbound_at=identity.last_inbound_at,
            candidate_timezone=app.timezone,
            opted_out=identity.opted_out,
            approved_template=template is not None and template.approved,
            proactive=True,
        )
        if decision.mode in ("blocked", "defer"):
            return ChannelOutcome(decision.reason)
        session = await self.conversations.repo.latest_session(tenant_id, app.id)
        if session is None and template is not None and template.purpose == "first_contact":
            token = await self.conversations.repo.get_or_create_link(tenant_id, app.id)
            opened = await self.conversations.open_session(token, "whatsapp")
            session = await self.conversations.repo.get_session(tenant_id, opened.session_id)
        if session is None or session.status != "active":
            return ChannelOutcome("session_closed")
        if decision.mode == "template" and template is not None and not simulated:
            text = f"[Plantilla {template.name} · {template.language}]"
        message = await self.conversations.repo.add_message(
            tenant_id, session.id, "out", text, channel="whatsapp", simulated=simulated
        )
        assert message is not None
        receipt = await self._send(
            identity, session.id, message, template if decision.mode == "template" else None
        )
        return ChannelOutcome(decision.mode, session.id, text, receipt)

    async def _send(
        self,
        identity: ChannelIdentityRecord,
        session_id: UUID,
        message: MessageRecord,
        template: ChannelTemplate | None,
    ) -> DeliveryReceipt:
        try:
            receipt = await self.adapter.send(
                OutboundMessage(
                    identity.tenant_id,
                    "whatsapp",
                    identity.contact,
                    message.text,
                    str(message.id),
                    session_id,
                    template,
                )
            )
        except Exception:
            await self.store.delivered(
                identity.tenant_id,
                message.id,
                DeliveryReceipt("", "failed", self.clock.now(), identity.simulated),
            )
            await self.conversations.repo.add_event(
                identity.tenant_id,
                session_id,
                "channel_delivery_needs_review",
                {"message_id": str(message.id), "status": "failed"},
            )
            raise
        await self.store.delivered(identity.tenant_id, message.id, receipt)
        await self.conversations.repo.add_event(
            identity.tenant_id,
            session_id,
            "channel_delivery",
            {
                "mode": "template" if template else "text",
                "simulated": receipt.simulated,
                "status": receipt.status,
                "at": receipt.recorded_at.isoformat(),
            },
        )
        return receipt
