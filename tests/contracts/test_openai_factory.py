import pytest
from langchain_core.messages import HumanMessage

from adapters.llm.openai import ROLE_EFFORT, ROLE_MAX_TOKENS, build_openai_model
from tests.scenarios.legacy_openai import recorded_luna_model


@pytest.mark.parametrize("role", ROLE_MAX_TOKENS)
def test_role_models_use_responses_effort_without_remote_storage(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-only-not-a-key")
    model = build_openai_model(role)
    expected_model = "gpt-6.1-terra"
    assert model.model_name == expected_model
    payload = model._get_request_payload([HumanMessage("Hola")])
    assert model.use_responses_api is True
    assert payload["model"] == expected_model
    expected_effort = "low" if role == "conversador" else "medium"
    assert ROLE_EFFORT[role] == expected_effort
    assert payload["reasoning"] == {"effort": expected_effort}
    assert payload["max_output_tokens"] == ROLE_MAX_TOKENS[role]
    assert payload["store"] is False
    assert "reasoning.encrypted_content" in payload["include"]
    assert "temperature" not in payload
    assert "top_p" not in payload


def test_effort_configuration_covers_exactly_the_supported_roles() -> None:
    assert ROLE_EFFORT.keys() == ROLE_MAX_TOKENS.keys()


def test_unknown_role_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-only-not-a-key")
    with pytest.raises(KeyError, match="unknown"):
        build_openai_model("unknown")


@pytest.mark.parametrize(
    ("role", "effort"), [("conversador", "low"), ("guardian", "low"), ("evaluador", "medium")]
)
def test_historical_recordings_keep_their_original_model_and_effort(
    role: str, effort: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "offline-only-not-a-key")
    payload = recorded_luna_model(role)._get_request_payload([HumanMessage("Hola")])
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"] == {"effort": effort}
