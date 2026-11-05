import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from adapters.agent_langchain.jev_guard import MODEL, RULES, JevReplyGuard
from adapters.fakes.guard import ScriptedReplyGuard
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.ports import GuardVerdict
from screening.domain.run_context import RunContext

pytestmark = pytest.mark.asyncio


def context() -> RunContext:
    return RunContext(
        "conversador",
        uuid4(),
        "demo",
        "tenant",
        "candidate",
        uuid4(),
        uuid4(),
        uuid4(),
        frozenset(),
        {},
    )


def response_data(probability: Any = 0.01) -> dict[str, Any]:
    return {
        "model": MODEL,
        "answers": {key: {"type": "boolean", "probability": probability} for key in RULES},
        "usage": {"inputTokens": 400, "outputTokens": 30},
        "providerMetadata": {"gateway": {"cost": "0.0000168"}},
    }


def make_guard(
    tmp_path: Path,
    data: dict[str, Any],
    *,
    synthetic: bool = True,
    synthetic_only: bool = True,
    status: int = 200,
) -> tuple[JevReplyGuard, InMemoryTraceSink, list[dict[str, Any]]]:
    key_file = tmp_path / "credential"
    key_file.write_text("test-only-key")
    sink = InMemoryTraceSink()
    calls = []

    async def is_synthetic(ctx: RunContext) -> bool:
        return synthetic

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "ai-gateway.vercel.sh"
        assert request.url.path == "/v1/evaluate"
        assert request.headers["authorization"] == "Bearer test-only-key"
        calls.append(json.loads(request.content))
        return httpx.Response(status, json=data)

    return (
        JevReplyGuard(
            str(key_file),
            ScriptedReplyGuard([GuardVerdict(approved=False, reason="second evaluator")]),
            sink,
            synthetic_only=synthetic_only,
            is_synthetic=is_synthetic,
            transport=httpx.MockTransport(handler),
        ),
        sink,
        calls,
    )


async def test_clear_reply_approved_and_no_text_in_trace(tmp_path: Path) -> None:
    guard, sink, calls = make_guard(tmp_path, response_data())
    result = await guard.review(context(), "private proposed reply", "private policy")
    assert result.approved
    assert len(calls) == 1
    assert set(calls[0]["questions"]) == set(RULES)
    assert calls[0]["providerOptions"]["gateway"]["disallowPromptTraining"] is True
    assert calls[0]["providerOptions"]["gateway"]["zeroDataRetention"] is False
    assert sink.llm_calls[0].cost_usd_micros == 17
    assert "private" not in str(sink.capability_calls)


@pytest.mark.parametrize("probability", [0.30, 0.9, 1])
async def test_any_rule_at_threshold_escalates(tmp_path: Path, probability: float) -> None:
    data = response_data()
    data["answers"]["privacy"]["probability"] = probability
    guard, sink, _ = make_guard(tmp_path, data)
    verdict = await guard.review(context(), "reply", "policy")
    assert not verdict.approved and verdict.reason == "second evaluator"
    assert sink.llm_calls[0].stop_reason == "escalated"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True, "0.1", None])
async def test_invalid_probability_never_approves(tmp_path: Path, value: Any) -> None:
    # Validate non-JSON floating values directly; the real response parser must reject them too.
    with pytest.raises(ValueError):
        JevReplyGuard._parse(response_data(value))


@pytest.mark.parametrize(
    "mutation", ["missing_rule", "wrong_model", "wrong_type", "bad_usage", "bad_cost"]
)
async def test_invalid_provider_response_escalates(tmp_path: Path, mutation: str) -> None:
    data = response_data()
    if mutation == "missing_rule":
        data["answers"].pop("all_rules")
    elif mutation == "wrong_model":
        data["model"] = "unexpected-fallback-model"
    elif mutation == "wrong_type":
        data["answers"]["privacy"]["type"] = "score"
    elif mutation == "bad_usage":
        data["usage"]["inputTokens"] = "100"
    else:
        data["providerMetadata"]["gateway"]["cost"] = "NaN"
    guard, _, _ = make_guard(tmp_path, data)
    result = await guard.review(context(), "reply", "policy")
    assert not result.approved and result.reason == "second evaluator"


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_http_failure_escalates_without_retry(tmp_path: Path, status: int) -> None:
    guard, _, calls = make_guard(tmp_path, {}, status=status)
    assert not (await guard.review(context(), "reply", "policy")).approved
    assert len(calls) == 1


async def test_real_candidate_not_sent_in_synthetic_mode(tmp_path: Path) -> None:
    guard, sink, calls = make_guard(tmp_path, response_data(), synthetic=False)
    assert not (await guard.review(context(), "real candidate", "policy")).approved
    assert not calls
    assert sink.capability_calls[0].response["reason"] == "retention_policy"


async def test_zdr_mode_explicitly_requests_no_retention(tmp_path: Path) -> None:
    guard, _, calls = make_guard(tmp_path, response_data(), synthetic_only=False)
    assert (await guard.review(context(), "reply", "policy")).approved
    assert calls[0]["providerOptions"]["gateway"]["zeroDataRetention"] is True


async def test_oversized_context_not_truncated_or_sent(tmp_path: Path) -> None:
    guard, _, calls = make_guard(tmp_path, response_data())
    assert not (await guard.review(context(), "reply", "a" * 60001)).approved
    assert not calls


async def test_both_models_unavailable_fails_closed(tmp_path: Path) -> None:
    class FailingFallback:
        async def review(self, ctx: RunContext, reply: str, sources: str) -> GuardVerdict:
            raise RuntimeError("private exception")

    guard, _, _ = make_guard(tmp_path, {}, status=500)
    guard.fallback = FailingFallback()
    verdict = await guard.review(context(), "reply", "policy")
    assert verdict == GuardVerdict(approved=False, reason="sin veredicto")
