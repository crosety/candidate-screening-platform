"""Explicit, private synthetic provisioning; does not publish client profiles."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError

from screening.platform.container import get_container
from screening.platform.voice_runtime import (
    DIRECT_MODEL,
    agent_registry_key,
    policy_hash,
    tool_specs,
    voice_prompt,
)


class Command(BaseCommand):
    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("tenant")
        parser.add_argument("session")
        parser.add_argument("--output", required=True)
        parser.add_argument("--voice-id", required=True)
        parser.add_argument("--key-file", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        async_to_sync(self.provision)(options)

    async def provision(self, options: dict[str, Any]) -> None:
        c = get_container()
        session = await c.conversations.repo.get_session(
            UUID(options["tenant"]), UUID(options["session"])
        )
        app = await c.conversations.repo.get_application(session.tenant_id, session.application_id)
        if not app.is_test:
            raise CommandError("Solo candidaturas sintéticas")
        api_key = (await asyncio.to_thread(Path(options["key_file"]).read_text)).strip()
        payload: dict[str, Any] = {
            "name": f"Orbio direct · {session.profile_version_id}",
            "conversation_config": {
                "realtime_model": DIRECT_MODEL,
                "agent": {
                    "language": "es",
                    "first_message": "{{orbio_opening}}",
                    "dynamic_variables": {
                        "dynamic_variable_placeholders": {
                            "orbio_context": "Sin historial",
                            "orbio_opening": "Hola",
                            "orbio_channel_context": "Canal de voz sin contexto; no prometas SMS.",
                        }
                    },
                    "prompt": {
                        "prompt": voice_prompt(session),
                        "llm": "gemini-2.5-flash",
                        "temperature": 0.2,
                        "max_tokens": 1200,
                        "enable_parallel_tool_calls": False,
                        "tools": tool_specs(session),
                        "backup_llm_config": {"preference": "disabled"},
                    },
                },
                "asr": {"user_input_audio_format": "pcm_16000"},
                "tts": {
                    "voice_id": options["voice_id"],
                    "model_id": "eleven_flash_v2_5",
                    "agent_output_audio_format": "pcm_16000",
                },
                "turn": {
                    "turn_timeout": 15,
                    "turn_eagerness": "normal",
                    "silence_end_call_timeout": 60,
                },
                "conversation": {
                    "max_duration_seconds": 300,
                    "client_events": [
                        "audio",
                        "interruption",
                        "user_transcript",
                        "agent_response",
                        "client_tool_call",
                    ],
                },
            },
            "platform_settings": {
                "auth": {"enable_auth": True},
                "privacy": {"record_voice": False, "retention_days": 7},
                "call_limits": {"agent_concurrency_limit": 2, "daily_limit": 30},
            },
        }
        path = Path(options["output"])
        registry = (
            json.loads(await asyncio.to_thread(path.read_text))
            if await asyncio.to_thread(path.exists)
            else {}
        )
        identity = agent_registry_key(session)
        if (
            identity in registry
            and registry[identity]["hash"] == policy_hash(session)
            and registry[identity].get("record_voice") is False
        ):
            self.stdout.write("Ya existe el agente con la misma política.")
            return
        async with httpx.AsyncClient(
            base_url="https://api.elevenlabs.io",
            headers={"xi-api-key": api_key},
            timeout=30,
        ) as client:
            if identity in registry:
                response = await client.patch(
                    f"/v1/convai/agents/{registry[identity]['agent_id']}", json=payload
                )
            else:
                response = await client.post("/v1/convai/agents/create", json=payload)
            mode = "realtime_max"
            if not response.is_success:
                # Validation details only, never request headers or our config containing secrets.
                detail = response.json().get("detail", {})
                if isinstance(detail, list):
                    detail = [{"loc": v.get("loc"), "msg": v.get("msg")} for v in detail]
                raise CommandError(
                    f"Provisioning HTTP {response.status_code}: {str(detail)[:1200]}"
                )
            agent_id = response.json()["agent_id"]
            check = await client.get(f"/v1/convai/agents/{agent_id}")
            check.raise_for_status()
            actual = check.json()
            if actual["platform_settings"]["auth"]["enable_auth"] is not True:
                raise CommandError("Agente sin autenticación: no se habilita")
            if actual["conversation_config"].get("realtime_model") != DIRECT_MODEL:
                raise CommandError("El proveedor no confirmó el modelo solicitado")
            if (
                actual.get("platform_settings", {}).get("privacy", {}).get("record_voice")
                is not False
            ):
                raise CommandError("El proveedor no confirmó la desactivación de grabaciones")
        registry[identity] = {
            "agent_id": agent_id,
            "hash": policy_hash(session),
            "mode": mode,
            "voice_id": options["voice_id"],
            "record_voice": False,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(registry, stream, indent=2)
        self.stdout.write(json.dumps({"agent_id": agent_id, "mode": mode, "private": True}))
