"""Allowlisted post-call measurements; never forwards transcripts or audio."""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from adapters.llm.langfuse import metric_span


def conversation_metrics(
    data: dict[str, Any],
    session: str,
    environment: str,
) -> list[dict[str, Any]]:
    if data.get("status") not in {"done", "failed"}:
        raise ValueError("Medición de voz pendiente")
    meta = data["metadata"]
    duration = int(float(meta["call_duration_secs"]) * 1000)
    end = datetime.fromtimestamp(meta["start_time_unix_secs"], UTC) + timedelta(
        milliseconds=duration
    )
    charging = meta.get("charging") or {}
    # Native speech-to-speech has no separately billed LLM tokens. Its reported
    # fiat cost is the voice service cost, not Twilio and not an invented token estimate.
    fiat = meta.get("cost_fiat")
    models = {
        turn.get("producing_llm")
        for turn in data.get("transcript", [])
        if turn.get("producing_llm")
    }
    model = "unknown-voice-model"
    if len(models) == 1:
        reported = next(iter(models))
        if isinstance(reported, str) and re.fullmatch(r"[a-zA-Z0-9_./:-]{1,100}", reported):
            model = reported
    common = dict(
        session=session,
        effort="provider",
        ended_at=end,
        latency_ms=duration,
        environment=environment,
        stop_reason=data["status"],
    )
    categories = charging.get("llm_usage") or {}
    spans = []
    for category in ("initiated_generation", "irreversible_generation"):
        for model_name, values in (categories.get(category) or {}).get("model_usage", {}).items():
            if not re.fullmatch(r"[a-zA-Z0-9_./:-]{1,100}", model_name):
                continue
            usage = {
                target: int(values[source]["tokens"])
                for source, target in (
                    ("input", "input"),
                    ("input_cache_read", "input_cache_read"),
                    ("input_cache_write", "input_cache_write"),
                    ("output_total", "output"),
                )
                if source in values and "tokens" in values[source]
            }
            # Categories may overlap; preserve their breakdown as metadata, never
            # sum them into billable totals. The provider total below is authoritative.
            spans.append(
                metric_span(
                    identity=f"voice:{session}:{data['conversation_id']}:{category}:{model_name}",
                    agent="voice_breakdown",
                    model=model_name,
                    usage=None,
                    cost_micros=0,
                    metadata={
                        "category": category,
                        "tokens_breakdown": json.dumps(usage),
                        "cost_basis": "excluded_from_totals",
                        "timing": "whole_call",
                    },
                    **common,
                )
            )
    spans.append(
        metric_span(
            identity=f"voice:{session}:{data['conversation_id']}:total",
            agent="voice",
            model=model,
            usage=None,
            cost_micros=int(Decimal(str(fiat)) * 1_000_000) if fiat is not None else None,
            metadata={
                "metrics_status": "provider_summary" if spans else "not_reported",
                "cost_basis": "voice_service_excluding_telephony",
                "timing": "whole_call",
                "tokens_status": "not_reported_as_billable_total",
            },
            **common,
        )
    )
    return spans
