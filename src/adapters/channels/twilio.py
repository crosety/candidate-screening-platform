"""Twilio Sandbox wire contract. No provider SDK or implicit retries."""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

from screening.domain.errors import NotAllowed


class TwilioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    account_sid: str = Field(pattern=r"^AC[0-9a-fA-F]{32}$")
    auth_token: SecretStr = Field(min_length=16)
    sender: str = Field(pattern=r"^\+[1-9][0-9]{6,14}$")
    public_base_url: str
    participants: list[str] = Field(min_length=1, max_length=10)
    enabled: bool = False
    expires_at: AwareDatetime | None = None
    max_outbound_per_day: int = Field(default=60, ge=1, le=100)

    @property
    def active(self) -> bool:
        return self.enabled and (self.expires_at is None or datetime.now(UTC) < self.expires_at)

    @field_validator("public_base_url")
    @classmethod
    def public_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Se necesita un origen HTTPS sin ruta ni credenciales")
        return value.rstrip("/")

    @field_validator("participants")
    @classmethod
    def phones(cls, values: list[str]) -> list[str]:
        import re

        if any(re.fullmatch(r"\+[1-9][0-9]{6,14}", v) is None for v in values):
            raise ValueError("Participantes en formato E.164")
        return sorted(set(values))

    def callback(self, kind: str) -> str:
        if kind not in {"inbound", "status"}:
            raise ValueError("Callback no válido")
        return f"{self.public_base_url}/api/v1/webhooks/twilio/{self.tenant_id}/{kind}"


def valid_twilio_signature(
    url: str, params: Mapping[str, list[str]], signature: str, secret: str
) -> bool:
    # Twilio signs the exact public URL and every form value (including new fields).
    value = url + "".join(k + v for k in sorted(params) for v in sorted(set(params[k])))
    expected = base64.b64encode(hmac.new(secret.encode(), value.encode(), hashlib.sha1).digest())
    return bool(secret) and hmac.compare_digest(expected, signature.encode())


class TwilioClient:
    def __init__(
        self, config: TwilioConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config, self.transport = config, transport

    async def send(self, contact: str, text: str) -> str:
        if not self.config.active or contact not in self.config.participants:
            raise NotAllowed("Envío Twilio no autorizado")
        if not text.strip() or len(text) > 1600:
            raise NotAllowed("Texto WhatsApp fuera de límite")
        async with httpx.AsyncClient(
            base_url="https://api.twilio.com",
            auth=(self.config.account_sid, self.config.auth_token.get_secret_value()),
            timeout=20,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"/2010-04-01/Accounts/{self.config.account_sid}/Messages.json",
                data={
                    "To": "whatsapp:" + contact,
                    "From": "whatsapp:" + self.config.sender,
                    "Body": text,
                    "StatusCallback": self.config.callback("status"),
                },
            )
        # Never log response bodies: they can contain contacts, text and credentials.
        if response.status_code != 201:
            raise NotAllowed(f"Twilio no confirmó el envío (HTTP {response.status_code})")
        data: dict[str, Any] = response.json()
        sid = str(data.get("sid", ""))
        import re

        if re.fullmatch(r"SM[0-9a-fA-F]{32}", sid) is None:
            raise NotAllowed("Twilio no confirmó el identificador del envío")
        return sid
