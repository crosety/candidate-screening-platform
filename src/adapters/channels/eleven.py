import base64
import hashlib
import hmac
import json
import math
import re
import time
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VoiceAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    api_key: SecretStr = Field(min_length=1)
    callback_secret: SecretStr = Field(min_length=1)
    callback_url: str = Field(min_length=1)


class ElevenAdapter:
    def __init__(
        self,
        config: VoiceAgent,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        expected_voice_id: str = "",
    ):
        self.config, self.transport = config, transport
        self.expected_voice_id = expected_voice_id

    async def signed_url(self) -> str:
        try:
            return await self._signed_url()
        except httpx.HTTPError:
            raise ValueError(
                "El proveedor de voz no está disponible. Inténtalo más tarde."
            ) from None
        except (AttributeError, TypeError):
            raise ValueError(
                "La configuración recibida del proveedor de voz no es válida."
            ) from None

    async def _signed_url(self) -> str:
        config = self.config
        async with httpx.AsyncClient(
            base_url="https://api.elevenlabs.io",
            timeout=20,
            follow_redirects=False,
            headers={"xi-api-key": config.api_key.get_secret_value()},
            transport=self.transport,
        ) as client:
            if config.agent_id.startswith("seng_"):
                return await self._speech_engine_url(client)
            agent = await client.get(f"/v1/convai/agents/{config.agent_id}")
            if agent.status_code != 200:
                raise ValueError("No se puede validar el agente de voz.")
            data = agent.json()
            conversation = data.get("conversation_config", {})
            tts = conversation.get("tts", {})
            actual_voice = tts.get("voice_id", "")
            if self.expected_voice_id and actual_voice != self.expected_voice_id:
                raise ValueError("La voz del agente no coincide con la versión del perfil.")
            if (
                conversation.get("asr", {}).get("user_input_audio_format") != "pcm_16000"
                or tts.get("agent_output_audio_format") != "pcm_16000"
            ):
                raise ValueError("Configura entrada y salida de voz en PCM de 16 kHz.")
            settings = conversation.get("agent", {})
            prompt = settings.get("prompt", {})
            custom = prompt.get("custom_llm", {})
            if (
                prompt.get("llm") != "custom-llm"
                # ElevenLabs appends /chat/completions to this OpenAI-style base URL.
                or custom.get("url", "").rstrip("/")
                != config.callback_url.rstrip("/").removesuffix("/chat/completions")
                or custom.get("api_type", "chat_completions") != "chat_completions"
                or settings.get("first_message", "")
                or prompt.get("backup_llm_config") != {"preference": "disabled"}
                or not data.get("platform_settings", {}).get("auth", {}).get("enable_auth")
                or data.get("platform_settings", {})
                .get("overrides", {})
                .get("custom_llm_extra_body")
                is not True
            ):
                raise ValueError(
                    "El agente debe ser privado, sin saludo ni modelo alternativo, "
                    "y usar nuestro callback."
                )
            response = await client.get(
                "/v1/convai/conversation/get-signed-url", params={"agent_id": config.agent_id}
            )
            if response.status_code != 200:
                raise ValueError("No se puede abrir la conversación de voz.")
            url = str(response.json().get("signed_url", ""))
            parsed = urlsplit(url)
            if parsed.scheme != "wss" or parsed.hostname != "api.elevenlabs.io":
                raise ValueError("Respuesta de voz no válida.")
            return url

    async def _speech_engine_url(self, client: httpx.AsyncClient) -> str:
        response = await client.get(f"/v1/speech-engine/{self.config.agent_id}")
        if response.status_code != 200:
            raise ValueError("No se puede validar Speech Engine.")
        engine = response.json()
        upstream = engine.get("speech_engine", {})
        tts = engine.get("tts", {})
        expected_url = self.config.callback_url.replace("https://", "wss://", 1)
        if (
            upstream.get("ws_url") != expected_url
            or upstream.get("request_headers", {}).get("X-Orbio-Session")
            != {"variable_name": "orbio_session_token"}
            or engine.get("asr", {}).get("user_input_audio_format") != "pcm_16000"
            or tts.get("agent_output_audio_format") != "pcm_16000"
            or (self.expected_voice_id and tts.get("voice_id") != self.expected_voice_id)
            or engine.get("overrides", {}).get("first_message", False)
        ):
            raise ValueError("Speech Engine no coincide con la configuración protegida.")
        response = await client.get(
            "/v1/convai/conversation/get-signed-url",
            params={"agent_id": self.config.agent_id},
        )
        if response.status_code != 200:
            raise ValueError("No se puede abrir Speech Engine.")
        url = str(response.json().get("signed_url", ""))
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or parsed.hostname != "api.elevenlabs.io":
            raise ValueError("Respuesta de voz no válida.")
        return url


def verify_speech_engine_jwt(value: str, api_key: str) -> bool:
    """Verify the documented upstream HS256 envelope before accepting audio work."""
    try:
        if len(value) > 8192:
            return False
        token = value.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        header, payload, signature = token.split(".")

        def decode(part: str) -> bytes:
            return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

        if json.loads(decode(header)).get("alg") != "HS256":
            return False
        expected = hmac.digest(
            hashlib.sha256(re.sub(r"_residency_[a-z]+$", "", api_key.strip()).encode()).digest(),
            f"{header}.{payload}".encode(),
            "sha256",
        )
        if not hmac.compare_digest(decode(signature), expected):
            return False
        claims = json.loads(decode(payload))
        expiry = claims.get("exp")
        issued = claims.get("iat")
        return (
            claims.get("iss") == "https://api.elevenlabs.io/convai/speech-engine"
            and claims.get("sub") == "convai_speech_engine_upstream"
            and isinstance(expiry, (int, float))
            and math.isfinite(expiry)
            and time.time() <= expiry + 60
            and isinstance(issued, (int, float))
            and math.isfinite(issued)
            and issued <= time.time() + 60
        )
    except (ValueError, TypeError, AttributeError):
        return False
