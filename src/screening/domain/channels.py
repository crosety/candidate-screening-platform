import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

ChannelName = Literal["web", "whatsapp", "voice"]
DeliveryStatus = Literal["pending", "sent", "delivered", "read", "failed"]
TemplatePurpose = Literal["first_contact", "followup"]


def template_values(keys: list[str], *, name: str, role: str, company: str) -> tuple[str, ...]:
    values = {
        "candidate_name": name,
        "name": name,
        "nombre": name,
        "role": role,
        "puesto": role,
        "company": company,
        "empresa": company,
    }
    if any(key not in values for key in keys):
        raise ValueError("La plantilla contiene una variable no admitida.")
    return tuple(values[key] for key in keys)


def normalize_contact(contact: str) -> str:
    if contact.startswith("sim:"):
        return contact
    value = re.sub(r"[\s().-]", "", contact).removeprefix("+")
    if value.startswith("00"):
        value = value[2:]
    if not re.fullmatch(r"[1-9][0-9]{6,14}", value):
        raise ValueError("Teléfono internacional no válido.")
    return "+" + value


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("La fecha debe incluir zona horaria.")


@dataclass(frozen=True)
class NormalizedInbound:
    tenant_id: UUID
    channel: ChannelName
    provider_message_id: str
    contact: str
    text: str
    received_at: datetime
    candidate_code: str | None = None

    def __post_init__(self) -> None:
        require_aware(self.received_at)
        if not self.provider_message_id.strip() or not self.contact.strip():
            raise ValueError("Falta la identidad del mensaje o del contacto.")


@dataclass(frozen=True)
class ChannelTemplate:
    name: str
    language: str
    purpose: TemplatePurpose
    variables: tuple[str, ...] = ()
    approved: bool = False


@dataclass(frozen=True)
class OutboundMessage:
    tenant_id: UUID
    channel: ChannelName
    contact: str
    text: str
    idempotency_key: str
    session_id: UUID | None = None
    template: ChannelTemplate | None = None


@dataclass(frozen=True)
class DeliveryReceipt:
    provider_message_id: str
    status: DeliveryStatus
    recorded_at: datetime
    simulated: bool = False

    def __post_init__(self) -> None:
        require_aware(self.recorded_at)


@dataclass(frozen=True)
class DeliveryPolicyDecision:
    mode: Literal["text", "template", "defer", "blocked"]
    reason: str
    send_at: datetime | None = None


def is_opt_out(text: str) -> bool:
    return text.strip().strip(" .!¡?¿").casefold() in {"baja", "stop"}


def whatsapp_delivery(
    *,
    now: datetime,
    last_inbound_at: datetime | None,
    candidate_timezone: str,
    opted_out: bool,
    approved_template: bool,
    proactive: bool,
) -> DeliveryPolicyDecision:
    """Decide transport eligibility without generating or sending any content."""
    if opted_out:
        return DeliveryPolicyDecision("blocked", "opted_out")
    require_aware(now)
    now_utc = now.astimezone(UTC)
    last_inbound_utc = None
    if last_inbound_at is not None:
        require_aware(last_inbound_at)
        last_inbound_utc = last_inbound_at.astimezone(UTC)
        if last_inbound_utc > now_utc:
            return DeliveryPolicyDecision("blocked", "future_inbound")

    within_window = last_inbound_utc is not None and now_utc - last_inbound_utc < timedelta(
        hours=24
    )
    if not within_window and not approved_template:
        return DeliveryPolicyDecision("blocked", "no_approved_template")

    local = now.astimezone(ZoneInfo(candidate_timezone))
    if proactive and (local.hour >= 22 or local.hour < 8):
        next_day = local.date() + timedelta(days=1) if local.hour >= 22 else local.date()
        send_at = datetime.combine(next_day, time(8), tzinfo=local.tzinfo).astimezone(UTC)
        return DeliveryPolicyDecision("defer", "quiet_hours", send_at)

    return DeliveryPolicyDecision(
        "text" if within_window else "template",
        "within_window" if within_window else "template_required",
    )
