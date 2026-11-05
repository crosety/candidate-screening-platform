from datetime import datetime
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import F

from screening.application.channels import ChannelIdentityRecord
from screening.domain.channels import DeliveryReceipt, normalize_contact
from screening.domain.errors import Conflict, NotAllowed, NotFound
from web.conversations.models import Application, ChannelIdentity, Message
from web.tenancy.context import tenant_context


def record(row: ChannelIdentity) -> ChannelIdentityRecord:
    return ChannelIdentityRecord(
        row.tenant_id,
        row.contact,
        row.application_id,
        row.opted_out,
        row.last_inbound_at,
        row.simulated,
        row.clock_offset_seconds,
    )


class DjangoChannelStore:
    @sync_to_async
    def identity(
        self, tenant_id: UUID, contact: str, simulated: bool = False
    ) -> ChannelIdentityRecord:
        contact = normalize_contact(contact)
        with tenant_context(tenant_id):
            row, _ = ChannelIdentity.objects.get_or_create(
                tenant_id=tenant_id,
                channel="whatsapp",
                contact=contact,
                defaults={"simulated": simulated},
            )
            if row.simulated != simulated:
                raise Conflict("No se puede mezclar el teléfono simulado con un contacto real.")
            return record(row)

    @sync_to_async
    def bind(
        self, tenant_id: UUID, contact: str, application_id: UUID, simulated: bool = False
    ) -> ChannelIdentityRecord:
        contact = normalize_contact(contact)
        with tenant_context(tenant_id):
            if not Application.objects.filter(tenant_id=tenant_id, pk=application_id).exists():
                raise NotFound("Candidatura no encontrada")
            row, _ = ChannelIdentity.objects.select_for_update().get_or_create(
                tenant_id=tenant_id,
                channel="whatsapp",
                contact=contact,
                defaults={"application_id": application_id, "simulated": simulated},
            )
            if row.application_id not in (None, application_id) or row.simulated != simulated:
                raise Conflict("El contacto ya está vinculado a otra candidatura o canal.")
            row.application_id = application_id
            row.save(update_fields=["application", "updated_at"])
            return record(row)

    @sync_to_async
    def inbound(self, tenant_id: UUID, contact: str, at: datetime) -> None:
        contact = normalize_contact(contact)
        with tenant_context(tenant_id):
            row = ChannelIdentity.objects.select_for_update().get(
                tenant_id=tenant_id, channel="whatsapp", contact=contact
            )
            if row.last_inbound_at is None or at > row.last_inbound_at:
                row.last_inbound_at = at
                row.save(update_fields=["last_inbound_at", "updated_at"])

    @sync_to_async
    def opt_out(self, tenant_id: UUID, contact: str) -> None:
        contact = normalize_contact(contact)
        with tenant_context(tenant_id):
            ChannelIdentity.objects.filter(
                tenant_id=tenant_id, channel="whatsapp", contact=contact
            ).update(opted_out=True)

    @sync_to_async
    def advance(self, tenant_id: UUID, contact: str, seconds: int) -> None:
        if not 1 <= seconds <= 7 * 86400:
            raise NotAllowed("El avance debe estar entre 1 segundo y 7 días.")
        with tenant_context(tenant_id):
            changed = ChannelIdentity.objects.filter(
                tenant_id=tenant_id, channel="whatsapp", contact=contact, simulated=True
            ).update(clock_offset_seconds=F("clock_offset_seconds") + seconds)
            if not changed:
                raise NotAllowed("Solo puede avanzarse el reloj de un teléfono simulado.")

    @sync_to_async
    def delivered(self, tenant_id: UUID, message_id: UUID, receipt: DeliveryReceipt) -> None:
        with tenant_context(tenant_id):
            changed = Message.objects.filter(tenant_id=tenant_id, pk=message_id).update(
                provider_message_id=receipt.provider_message_id,
                delivery_status=receipt.status,
                simulated=receipt.simulated,
            )
            if not changed:
                raise NotFound("Mensaje no encontrado")
            if receipt.provider_message_id and not receipt.simulated:
                from web.conversations.meta_webhook import apply_status

                apply_status(tenant_id, receipt.provider_message_id, receipt.status)
