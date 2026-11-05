"""One-way SMS links. No implicit retries, inbound chat, or branded sender."""

import ipaddress
import math
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from adapters.channels.twilio import TwilioConfig
from adapters.channels.twilio_voice import TwilioVoiceConfig
from screening.domain.errors import NotAllowed

SEGMENT_PRICE = 87_500
# ASCII-only templates deliberately avoid ambiguous Unicode segment accounting.
GSM_BASIC = set(
    "\n\r !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"
)


def segments(text: str) -> int:
    if not text or any(c not in GSM_BASIC for c in text):
        raise NotAllowed("El aviso SMS debe usar la plantilla compatible y sin Unicode")
    return 1 if len(text) <= 160 else math.ceil(len(text) / 153)


class SmsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    enabled: bool = False
    candidate_base_url: str
    callback_base_url: str
    expires_at: AwareDatetime
    max_segments: int = Field(default=30, ge=1, le=30)

    @field_validator("candidate_base_url", "callback_base_url")
    @classmethod
    def https_origin(cls, value: str) -> str:
        value = TwilioConfig.public_url(value)
        host = urlsplit(value).hostname or ""
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("El enlace debe ser accesible desde el móvil")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("El enlace debe ser público")
        return value

    @property
    def active(self) -> bool:
        return self.enabled and datetime.now(UTC) < self.expires_at

    def callback(self, notification: UUID) -> str:
        root = f"{self.callback_base_url}/api/v1/webhooks/twilio-sms/{self.tenant_id}"
        return f"{root}/{notification}/status"


class SmsOutcomeUnknown(Exception):
    """The provider may have accepted the send. Do not repeat it."""


class TwilioSmsClient:
    def __init__(
        self,
        account: TwilioVoiceConfig,
        config: SmsConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.account, self.config, self.transport = account, config, transport

    async def send(self, contact: str, text: str, notification: UUID) -> str:
        if not self.config.active or contact not in self.account.participants:
            raise NotAllowed("SMS no autorizado")
        if segments(text) > 3:
            raise NotAllowed("El aviso excede tres segmentos")
        try:
            async with httpx.AsyncClient(
                base_url="https://api.twilio.com",
                timeout=20,
                follow_redirects=False,
                auth=(self.account.account_sid, self.account.auth_token.get_secret_value()),
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"/2010-04-01/Accounts/{self.account.account_sid}/Messages.json",
                    data={
                        "To": contact,
                        "From": self.account.sender,
                        "Body": text,
                        "StatusCallback": self.config.callback(notification),
                    },
                )
        except httpx.HTTPError:
            raise SmsOutcomeUnknown from None
        if response.status_code >= 500 or response.status_code == 429:
            raise SmsOutcomeUnknown
        if response.status_code != 201:
            raise NotAllowed("El proveedor rechazó el SMS")
        try:
            sid = str(response.json()["sid"])
        except (ValueError, KeyError, TypeError):
            raise SmsOutcomeUnknown from None
        if not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid):
            raise SmsOutcomeUnknown
        return sid
