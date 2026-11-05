import hashlib
import hmac
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from screening.application.ports import Clock
from screening.domain.channels import (
    ChannelName,
    ChannelTemplate,
    DeliveryReceipt,
    NormalizedInbound,
    OutboundMessage,
    normalize_contact,
)
from screening.domain.errors import NotAllowed


class MetaSender(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number_id: str = Field(pattern=r"^[0-9]+$")
    business_account_id: str = Field(pattern=r"^[0-9]+$")
    graph_version: str = Field(pattern=r"^v[0-9]+\.[0-9]+$")
    access_token: SecretStr = Field(min_length=1)
    app_secret: SecretStr = Field(min_length=1)
    verify_token: SecretStr = Field(min_length=1)


def valid_signature(body: bytes, signature: str, secret: str) -> bool:
    if re.fullmatch(r"sha256=[a-f0-9]{64}", signature) is None:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(expected, signature)


def normalize_meta(
    body: dict[str, Any], tenant_id: UUID, phone_number_id: str
) -> tuple[list[NormalizedInbound], list[dict[str, str]]]:
    incoming, statuses = [], []
    if body.get("object") != "whatsapp_business_account":
        raise ValueError("Objeto de webhook no válido.")
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if value.get("metadata", {}).get("phone_number_id") != phone_number_id:
                raise NotAllowed("El emisor del webhook no corresponde a este cliente.")
            for row in value.get("messages", []):
                kind = row.get("type")
                if kind == "text":
                    text = row["text"]["body"]
                elif kind == "button":
                    text = row["button"]["text"]
                elif kind == "interactive":
                    interactive = row["interactive"]
                    selected = (
                        interactive.get("button_reply") or interactive.get("list_reply") or {}
                    )
                    text = selected.get("title", "[Contenido no compatible]")
                else:
                    text = "[Contenido no compatible]"
                code = re.fullmatch(r"(?:CODIGO|CÓDIGO|CODE)\s+([A-Za-z0-9_-]{30,64})", text)
                incoming.append(
                    NormalizedInbound(
                        tenant_id,
                        "whatsapp",
                        row["id"],
                        normalize_contact(row["from"]),
                        "Quiero continuar con mi candidatura." if code else text,
                        datetime.fromtimestamp(int(row["timestamp"]), UTC),
                        code.group(1) if code else None,
                    )
                )
            for row in value.get("statuses", []):
                if row.get("status") in ("sent", "delivered", "read", "failed"):
                    statuses.append({"id": row["id"], "status": row["status"]})
    return incoming, statuses


class MetaWhatsAppAdapter:
    channel: ChannelName = "whatsapp"

    def __init__(
        self,
        sender: MetaSender,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self.sender, self.clock, self.transport = sender, clock, transport
        self.enabled = enabled

    async def _request(self, method: str, resource: str, **kwargs: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=f"https://graph.facebook.com/{self.sender.graph_version}/",
            headers={"Authorization": f"Bearer {self.sender.access_token.get_secret_value()}"},
            timeout=20,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            response = await client.request(method, resource, **kwargs)
            if response.status_code >= 300:
                # Provider bodies can contain personal data; never put them in job errors.
                raise NotAllowed(f"Meta ha rechazado la operación (HTTP {response.status_code}).")
            data: dict[str, Any] = response.json()
            return data

    async def approved(self, template: ChannelTemplate) -> bool:
        data = await self._request(
            "GET",
            f"{self.sender.business_account_id}/message_templates",
            params={"name": template.name, "fields": "name,status,language"},
        )
        return any(
            row.get("name") == template.name
            and row.get("language") == template.language
            and row.get("status") == "APPROVED"
            for row in data.get("data", [])
        )

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if not self.enabled:
            raise NotAllowed("Los envíos externos de Meta están desactivados.")
        contact = message.contact.removeprefix("+")
        if not re.fullmatch(r"[1-9][0-9]{6,14}", contact):
            raise NotAllowed("El contacto no tiene un formato de teléfono válido.")
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": contact,
        }
        if message.template is not None:
            if not await self.approved(message.template):
                raise NotAllowed("La plantilla no está aprobada por Meta para este emisor.")
            payload.update(
                type="template",
                template={
                    "name": message.template.name,
                    "language": {"code": message.template.language},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": value}
                                for value in message.template.variables
                            ],
                        }
                    ]
                    if message.template.variables
                    else [],
                },
            )
        else:
            if len(message.text) > 4096:
                raise NotAllowed("El mensaje supera el límite del canal.")
            payload.update(type="text", text={"preview_url": False, "body": message.text})
        data = await self._request("POST", f"{self.sender.phone_number_id}/messages", json=payload)
        try:
            provider_id = data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError):
            raise NotAllowed("Meta no ha devuelto el identificador del envío.") from None
        # Acceptance is not delivery: only subsequent signed webhooks can advance it.
        return DeliveryReceipt(str(provider_id), "sent", self.clock.now())
