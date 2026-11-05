from dataclasses import replace

from screening.application.channels import ChannelStore
from screening.application.ports import Clock
from screening.domain.channels import ChannelName, DeliveryReceipt, OutboundMessage


class SimulatedWhatsAppAdapter:
    channel: ChannelName = "whatsapp"

    def __init__(self, store: ChannelStore, clock: Clock) -> None:
        self.store, self.clock = store, clock
        self.sent: dict[str, DeliveryReceipt] = {}

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        from datetime import timedelta

        identity = await self.store.identity(message.tenant_id, message.contact, True)
        if message.idempotency_key not in self.sent:
            self.sent[message.idempotency_key] = DeliveryReceipt(
                f"sim:{message.idempotency_key}",
                "delivered",
                self.clock.now() + timedelta(seconds=identity.clock_offset_seconds),
                True,
            )
        return replace(self.sent[message.idempotency_key])
