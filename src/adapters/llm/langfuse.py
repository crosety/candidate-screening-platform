"""Metrics-only OTLP export. No prompts, completions or provider error bodies."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

HOSTS = {
    "https://cloud.langfuse.com",
    "https://us.cloud.langfuse.com",
    "https://jp.cloud.langfuse.com",
}


def session_identifier(identity: str) -> str:
    """Shared by OTLP export and the private console's deep link."""
    return hashlib.sha256(identity.encode()).hexdigest()


def session_url(host: str, project_id: str, identity: str) -> str | None:
    if host not in HOSTS or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project_id):
        return None
    return f"{host}/project/{project_id}/sessions/{session_identifier(identity)}"


class MetricsExportError(Exception):
    """Safe to persist in the worker's error field."""


@dataclass(frozen=True)
class LangfuseConfig:
    host: str
    project_id: str
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def load(
        cls, path: str, project_id: str, host: str = "https://cloud.langfuse.com"
    ) -> "LangfuseConfig":
        try:
            data = json.loads(Path(path).read_text())
            config = cls(**data)
            if (
                config.host != host
                or session_url(host, project_id, "") is None
                or not config.public_key
                or not config.secret_key
                or not project_id
                or config.project_id != project_id
            ):
                raise ValueError
            return config
        except (OSError, ValueError, TypeError):
            raise MetricsExportError("Configuración de métricas no válida") from None


def metric_span(
    *,
    identity: str,
    session: str,
    agent: str,
    model: str,
    effort: str,
    ended_at: datetime,
    latency_ms: int,
    usage: dict[str, int] | None,
    cost_micros: int | None,
    environment: str,
    stop_reason: str,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Only identifiers and explicitly selected numeric metrics may cross this boundary."""
    trace = hashlib.sha256(identity.encode()).hexdigest()[:32]
    attrs = {
        "langfuse.trace.name": f"orbio.{agent}",
        "langfuse.session.id": session_identifier(session),
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": model,
        "langfuse.observation.model.parameters": json.dumps({"reasoning_effort": effort}),
        "langfuse.observation.metadata.agent": agent,
        "langfuse.observation.metadata.source": "orbio",
        "langfuse.observation.metadata.stop_reason": (
            stop_reason if re.fullmatch(r"[a-z_]{1,40}", stop_reason) else "unknown"
        ),
        "langfuse.environment": environment,
    }
    if usage is not None:
        attrs["langfuse.observation.usage_details"] = json.dumps(usage)
    if cost_micros is not None:
        attrs["langfuse.observation.cost_details"] = json.dumps({"total": cost_micros / 1_000_000})
    for key, value in (metadata or {}).items():
        attrs[f"langfuse.observation.metadata.{key}"] = value
    start = ended_at - timedelta(milliseconds=latency_ms)
    return {
        "traceId": trace,
        "spanId": trace[:16],
        "name": f"orbio.{agent}",
        "kind": 1,
        "startTimeUnixNano": str(int(start.timestamp() * 1_000_000) * 1000),
        "endTimeUnixNano": str(int(ended_at.timestamp() * 1_000_000) * 1000),
        "attributes": [
            {"key": key, "value": {"stringValue": value}} for key, value in attrs.items()
        ],
    }


class LangfuseExporter:
    def __init__(
        self,
        config: LangfuseConfig,
        *,
        voice_key_file: str = "",
        environment: str = "local",
    ) -> None:
        self.config = config
        self.voice_key_file = voice_key_file
        self.environment = environment

    def received(self, spans: list[dict[str, Any]]) -> set[str]:
        """Read receipts before dispatch/recovery: v4 does not deduplicate repeated IDs."""
        found: set[str] = set()
        try:
            with httpx.Client(
                base_url=self.config.host,
                auth=(self.config.public_key, self.config.secret_key),
                timeout=10,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                check = client.get("/api/public/projects")
                if check.status_code != 200 or [
                    p.get("id") for p in check.json().get("data", [])
                ] != [self.config.project_id]:
                    raise MetricsExportError("No se pudo verificar el proyecto de métricas")
                for span in spans:
                    start = datetime.fromtimestamp(int(span["startTimeUnixNano"]) / 1e9, UTC)
                    response = client.get(
                        "/api/public/v2/observations",
                        params={
                            "fromStartTime": (start - timedelta(seconds=1)).isoformat(),
                            "toStartTime": (start + timedelta(seconds=1)).isoformat(),
                            "limit": 2,
                            "fields": "core",
                            "filter": json.dumps(
                                [
                                    {
                                        "type": "string",
                                        "column": "id",
                                        "operator": "=",
                                        "value": span["spanId"],
                                    }
                                ]
                            ),
                        },
                    )
                    if response.status_code != 200:
                        raise MetricsExportError("No se pudo verificar el recibo de métricas")
                    if any(
                        row.get("id") == span["spanId"] and row.get("traceId") == span["traceId"]
                        for row in response.json()["data"]
                    ):
                        found.add(span["spanId"])
            return found
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
            raise MetricsExportError("No se pudo verificar el recibo de métricas") from None

    def measure_voice(self, conversation_id: str, session: str) -> list[dict[str, Any]]:
        from adapters.llm.eleven_metrics import conversation_metrics

        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", conversation_id):
            raise MetricsExportError("Identificador de métricas de voz no válido")
        try:
            key = Path(self.voice_key_file).read_text().strip()
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
                response = client.get(
                    f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                    headers={"xi-api-key": key},
                )
            if response.status_code != 200:
                raise MetricsExportError(f"Medición de voz: HTTP {response.status_code}")
            data = response.json()
            if data.get("conversation_id") != conversation_id:
                raise ValueError
            return conversation_metrics(data, session, self.environment)
        except (OSError, httpx.HTTPError, ValueError, TypeError, KeyError):
            raise MetricsExportError("Métricas de voz pendientes o no disponibles") from None

    def export(self, spans: list[dict[str, Any]]) -> None:
        if not spans:
            raise MetricsExportError("No hay métricas para exportar")
        try:
            with httpx.Client(
                base_url=self.config.host,
                auth=(self.config.public_key, self.config.secret_key),
                timeout=httpx.Timeout(10),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                check = client.get("/api/public/projects")
                if check.status_code != 200:
                    raise MetricsExportError(f"Verificación del proyecto: HTTP {check.status_code}")
                projects = check.json().get("data", [])
                if [p.get("id") for p in projects] != [self.config.project_id]:
                    raise MetricsExportError("La clave no corresponde exclusivamente al proyecto")
                response = client.post(
                    "/api/public/otel/v1/traces",
                    headers={
                        "x-langfuse-ingestion-version": "4",
                        "content-type": "application/json",
                    },
                    content=json.dumps(
                        {
                            "resourceSpans": [
                                {
                                    "resource": {
                                        "attributes": [
                                            {
                                                "key": "service.name",
                                                "value": {"stringValue": "orbio-screening"},
                                            },
                                        ]
                                    },
                                    "scopeSpans": [
                                        {"scope": {"name": "orbio.metrics"}, "spans": spans}
                                    ],
                                }
                            ]
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                if response.status_code != 200:
                    raise MetricsExportError(
                        f"Exportación de métricas: HTTP {response.status_code}"
                    )
                partial = response.json().get("partialSuccess", {})
                if int(partial.get("rejectedSpans", 0)) or partial.get("errorMessage"):
                    raise MetricsExportError("Langfuse no ha aceptado todas las métricas")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
            raise MetricsExportError("No se pudo verificar la entrega de métricas") from None
