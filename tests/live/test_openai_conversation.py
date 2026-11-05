from pathlib import Path

import pytest

from tests.scenarios.openai_conversation import exercise

pytestmark = [pytest.mark.asyncio, pytest.mark.live, pytest.mark.vcr]


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(Path(__file__).resolve().parents[1] / "cassettes")


@pytest.fixture(scope="module")
def default_cassette_name() -> str:
    return "openai_conversation_interview_opening"


async def test_real_conversation() -> None:
    _, sink = await exercise()
    print(
        {
            "cost_usd_micros": sum(call.cost_usd_micros for call in sink.llm_calls),
            "calls": [
                {
                    "agent": call.agent,
                    "effort": call.effort,
                    "latency_ms": call.latency_ms,
                    "input": call.input_tokens,
                    "output": call.output_tokens,
                    "cache_read": call.cache_read_tokens,
                    "cache_write": call.cache_write_tokens,
                }
                for call in sink.llm_calls
            ],
        }
    )
