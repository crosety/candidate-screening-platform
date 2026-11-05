from pathlib import Path

import pytest

from tests.scenarios.evaluation import exercise

pytestmark = [pytest.mark.asyncio, pytest.mark.live, pytest.mark.vcr]


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(Path(__file__).resolve().parents[1] / "cassettes")


@pytest.fixture(scope="module")
def default_cassette_name() -> str:
    return "openai_evaluator_interview_opening"


async def test_real_evaluator() -> None:
    sink = await exercise()
    print({"cost_usd_micros": sum(call.cost_usd_micros for call in sink.llm_calls)})
