"""Telephone wire contract used by the consented, private callback service.

The caller must first persist a consented, budget-reserved, single-use attempt.
This transport never retries an uncertain POST and does not itself manage quotas.
"""

import audioop
import base64
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

from screening.domain.errors import NotAllowed

SPANISH_MOBILE = re.compile(r"^\+34(?:6[0-9]{8}|7[1-4][0-9]{7})$")
CALL_SID = re.compile(r"^CA[0-9a-fA-F]{32}$")


class TwilioVoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    account_sid: str = Field(pattern=r"^AC[0-9a-fA-F]{32}$")
    auth_token: SecretStr = Field(min_length=16)
    sender: str = Field(pattern=r"^\+1[0-9]{10}$")
    public_base_url: str
    # Retained for SMS's separate recipient policy; calls use consented Spanish mobiles.
    participants: tuple[str, ...] = Field(default=(), max_length=10)
    enabled: bool = False
    expires_at: AwareDatetime
    max_seconds: int = Field(default=300, ge=30, le=300)
    max_attempts_per_mobile: int = Field(default=10, ge=1, le=10)

    @property
    def active(self) -> bool:
        return self.enabled and datetime.now(UTC) < self.expires_at

    @field_validator("public_base_url")
    @classmethod
    def origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Se necesita un origen HTTPS sin ruta ni credenciales")
        return value.rstrip("/")

    @field_validator("participants")
    @classmethod
    def mobiles(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not SPANISH_MOBILE.fullmatch(phone) for phone in values):
            raise ValueError("Solo móviles españoles invitados; sin números especiales")
        return tuple(sorted(set(values)))

    def callback(self, attempt_id: UUID, kind: str) -> str:
        if kind not in {"answer", "status", "media"}:
            raise ValueError("Callback no válido")
        origin = self.public_base_url
        if kind == "media":
            origin = origin.replace("https://", "wss://", 1)
        return f"{origin}/api/v1/webhooks/twilio-voice/{self.tenant_id}/{attempt_id}/{kind}"


class CallOutcomeUnknown(Exception):
    """Do not retry. Reconcile this attempt with the provider's signed callbacks."""


class TwilioVoiceClient:
    def __init__(
        self, config: TwilioVoiceConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config, self.transport = config, transport

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.twilio.com",
            auth=(self.config.account_sid, self.config.auth_token.get_secret_value()),
            timeout=20,
            follow_redirects=False,
            transport=self.transport,
        )

    async def preflight(self) -> None:
        if not self.config.active:
            raise NotAllowed("Telefonía desactivada o autorización caducada")
        async with self.client() as client:
            account = await client.get(f"/2010-04-01/Accounts/{self.config.account_sid}.json")
            if account.status_code != 200:
                raise NotAllowed("No se puede verificar la cuenta de telefonía")
            data = account.json()
            if (
                data.get("sid") != self.config.account_sid
                or data.get("type") != "Full"
                or data.get("status") != "active"
            ):
                raise NotAllowed("Se necesita una cuenta de pago activa")
            numbers = await client.get(
                f"/2010-04-01/Accounts/{self.config.account_sid}/IncomingPhoneNumbers.json",
                params={"PhoneNumber": self.config.sender, "PageSize": 50},
            )
            if numbers.status_code != 200 or not any(
                number.get("account_sid") == self.config.account_sid
                and number.get("phone_number") == self.config.sender
                and number.get("capabilities", {}).get("voice") is True
                for number in numbers.json().get("incoming_phone_numbers", [])
            ):
                raise NotAllowed("No se ha verificado un número propio con voz")

    async def stop(self, sid: str) -> bool:
        # Stopping an existing call remains permitted after the window expires.
        if not CALL_SID.fullmatch(sid):
            return False
        async with self.client() as client:
            response = await client.post(
                f"/2010-04-01/Accounts/{self.config.account_sid}/Calls/{sid}.json",
                data={"Status": "completed"},
            )
        return response.status_code == 200

    async def dial(
        self, contact: str, attempt_id: UUID, *, not_after: datetime | None = None
    ) -> str:
        """Exactly one POST. The durable dispatcher must consume attempt_id first."""
        if not self.config.active or not SPANISH_MOBILE.fullmatch(contact):
            raise NotAllowed("Destino telefónico no autorizado")
        await self.preflight()
        # The permission can expire while preflight is in flight.
        if not self.config.active:
            raise NotAllowed("Autorización telefónica caducada")
        if not_after is not None and datetime.now(UTC) >= not_after:
            raise NotAllowed("El horario autorizado de llamadas ha terminado")
        try:
            async with self.client() as client:
                response = await client.post(
                    f"/2010-04-01/Accounts/{self.config.account_sid}/Calls.json",
                    data={
                        "To": contact,
                        "From": self.config.sender,
                        "Url": self.config.callback(attempt_id, "answer"),
                        "Method": "POST",
                        "StatusCallback": self.config.callback(attempt_id, "status"),
                        "StatusCallbackMethod": "POST",
                        "Timeout": "15",
                        "TimeLimit": str(self.config.max_seconds),
                        "Record": "false",
                    },
                )
        except httpx.TransportError:
            # Don't include URL, destination, body or auth in the exception.
            raise CallOutcomeUnknown("Resultado de la llamada desconocido; no reintentar") from None
        if response.status_code != 201:
            if response.status_code >= 500 or response.status_code < 400:
                raise CallOutcomeUnknown("Twilio no confirmó el intento; no reintentar")
            raise NotAllowed(f"Twilio rechazó el intento (HTTP {response.status_code})")
        try:
            data: dict[str, Any] = response.json()
            sid = data.get("sid", "")
            if (
                not isinstance(sid, str)
                or not CALL_SID.fullmatch(sid)
                or data.get("account_sid") != self.config.account_sid
                or data.get("to") != contact
                or data.get("from") != self.config.sender
            ):
                raise ValueError
        except (ValueError, AttributeError):
            raise CallOutcomeUnknown("Respuesta de llamada no verificable; no reintentar") from None
        return sid


class TelephoneCodec:
    """Streaming converter for Python 3.12's stdlib audioop (pinned by the project).

    Independent resampler state in each direction avoids frame-boundary clicks.
    Nothing is written to disk. This is not an audible end-to-end acceptance test.
    """

    def __init__(self) -> None:
        self.in_state: Any = None
        self.out_state: Any = None

    def to_agent(self, payload: str) -> str:
        mulaw = base64.b64decode(payload, validate=True)
        if not 0 < len(mulaw) <= 8000:
            raise ValueError("Trama telefónica fuera de límite")
        pcm = audioop.ulaw2lin(mulaw, 2)
        converted, self.in_state = audioop.ratecv(pcm, 2, 1, 8000, 16000, self.in_state)
        return base64.b64encode(converted).decode("ascii")

    def to_phone(self, payload: str) -> str:
        pcm = base64.b64decode(payload, validate=True)
        if not 0 < len(pcm) <= 160000 or len(pcm) % 2:
            raise ValueError("Trama de voz fuera de límite")
        # Filter before downsampling, retaining the filter history across frames.
        converted, self.out_state = audioop.ratecv(pcm, 2, 1, 16000, 8000, self.out_state, 1, 1)
        return base64.b64encode(audioop.lin2ulaw(converted, 2)).decode("ascii")
