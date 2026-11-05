from pathlib import Path

import pytest

from tests.scenarios.evaluation import exercise

pytestmark = [pytest.mark.asyncio, pytest.mark.vcr]


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(Path(__file__).resolve().parents[1] / "cassettes")


@pytest.fixture(scope="module")
def default_cassette_name() -> str:
    return "openai_evaluator_interview_opening"


async def test_evaluator_replays_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-replay-not-a-key")
    await exercise()
