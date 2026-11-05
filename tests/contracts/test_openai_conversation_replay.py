from pathlib import Path

import pytest

from tests.scenarios.openai_conversation import exercise

CASSETTE_DIR = Path(__file__).resolve().parents[1] / "cassettes"
pytestmark = [pytest.mark.asyncio, pytest.mark.vcr]


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return str(CASSETTE_DIR)


@pytest.fixture(scope="module")
def default_cassette_name() -> str:
    return "openai_conversation_interview_opening"


@pytest.fixture(autouse=True)
def offline_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-replay-not-a-key")
    for key in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)


async def test_conversation_replays_without_credentials() -> None:
    assert (CASSETTE_DIR / "openai_conversation_interview_opening.yaml").exists(), (
        "Falta el casete obligatorio"
    )
    _, sink = await exercise()
    assert all(call.effort == "low" for call in sink.llm_calls)
