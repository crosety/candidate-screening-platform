import pytest

from adapters.llm.bedrock import MODEL_IDS, build_chat_model


def test_conversador_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    model = build_chat_model("conversador")
    assert model.model == MODEL_IDS["conversador"]
    assert model.max_tokens == 2048
