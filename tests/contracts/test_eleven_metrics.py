import json
from datetime import UTC, datetime

import pytest

from adapters.llm.eleven_metrics import conversation_metrics


def test_voice_breakdowns_are_not_added_twice_to_billable_totals() -> None:
    data = {
        "status": "done",
        "conversation_id": "conv_test",
        "transcript": [{"message": "PRIVATE"}],
        "metadata": {
            "start_time_unix_secs": 1793448000,
            "call_duration_secs": 60,
            "cost_fiat": 0.125,
            "phone_call": {"external_number": "PRIVATE"},
            "charging": {
                "llm_usage": {
                    "initiated_generation": {
                        "model_usage": {
                            "voice-model": {
                                "input": {"tokens": 20, "price": 0.002},
                                "output_total": {"tokens": 10, "price": 0.003},
                            }
                        }
                    },
                    "irreversible_generation": {
                        "model_usage": {
                            "voice-model": {
                                "input": {"tokens": 100, "price": 0.01},
                                "output_total": {"tokens": 40, "price": 0.012},
                            }
                        }
                    },
                }
            },
        },
    }
    spans = conversation_metrics(data, "tenant:session", "test")
    assert len(spans) == 3
    assert "PRIVATE" not in str(spans)
    assert spans[0]["traceId"] != spans[1]["traceId"]
    assert spans == conversation_metrics(data, "tenant:session", "test")
    attrs = [{a["key"]: a["value"]["stringValue"] for a in span["attributes"]} for span in spans]
    assert [json.loads(a["langfuse.observation.cost_details"]) for a in attrs] == [
        {"total": 0},
        {"total": 0},
        {"total": 0.125},
    ]
    assert all("langfuse.observation.usage_details" not in a for a in attrs)
    assert json.loads(attrs[0]["langfuse.observation.metadata.tokens_breakdown"]) == {
        "input": 20,
        "output": 10,
    }
    assert json.loads(attrs[1]["langfuse.observation.metadata.tokens_breakdown"]) == {
        "input": 100,
        "output": 40,
    }


def test_missing_voice_token_metrics_are_unknown_not_zero() -> None:
    spans = conversation_metrics(
        {
            "status": "done",
            "conversation_id": "conv_test",
            "metadata": {
                "start_time_unix_secs": int(datetime(2026, 10, 31, tzinfo=UTC).timestamp()),
                "call_duration_secs": 60,
            },
        },
        "tenant:session",
        "test",
    )
    attrs = {a["key"]: a["value"]["stringValue"] for a in spans[0]["attributes"]}
    assert "langfuse.observation.usage_details" not in attrs
    assert "langfuse.observation.cost_details" not in attrs
    assert attrs["langfuse.observation.metadata.metrics_status"] == "not_reported"
    assert attrs["langfuse.observation.model.name"] == "unknown-voice-model"


def test_processing_voice_metrics_must_be_retried() -> None:
    with pytest.raises(ValueError, match="pendiente"):
        conversation_metrics({"status": "processing"}, "tenant:session", "test")
