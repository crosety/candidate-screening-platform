import pytest

from adapters.agent_langchain.jev_guard import JevReplyGuard
from adapters.agent_langchain.runner import LangChainAgentRunner
from adapters.fakes.agent import EchoAgentRunner
from adapters.fakes.chat_model import ScriptedChatModel
from adapters.persistence.conversations import DjangoConversationRepository
from adapters.persistence.profiles import DjangoProfileRepository
from adapters.persistence.tracing import DjangoTraceSink
from screening.platform.container import build_container


@pytest.mark.parametrize("mode", ["fake", "bedrock", "replay", "openai"])
def test_persistence_is_django_in_every_mode(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    roles: list[str] = []

    def model(role: str) -> ScriptedChatModel:
        roles.append(role)
        return ScriptedChatModel(responses=[])

    monkeypatch.setattr("screening.platform.container.build_chat_model", model)
    monkeypatch.setattr("screening.platform.container.build_openai_model", model)
    container = build_container(mode)
    assert isinstance(container.profiles.repo, DjangoProfileRepository)
    assert isinstance(container.conversations.repo, DjangoConversationRepository)
    assert isinstance(container.sink, DjangoTraceSink)
    if mode == "fake":
        assert isinstance(container.conversations.runner, EchoAgentRunner)
        assert roles == []
    else:
        assert isinstance(container.conversations.runner, LangChainAgentRunner)
        # Only the active provider classifies turn signals with a model.
        assert roles == [
            "evaluador",
            "simulador",
            "juez",
            "copiloto",
            *(["senales"] if mode == "openai" else []),
            "conversador",
            "guardian",
            "copiloto",
        ]
        if mode == "openai":
            assert container.conversations.runner.model_id == "gpt-6.1-terra"
            assert container.conversations.runner.effort == "low"
            for component in (
                container.conversations.runner.guard,
                container.conversations.signals,
                container.assessment.evaluator,
                container.evals.simulator,
                container.evals.judge,
                container.copilot.copilot,
                container.recruiter,
            ):
                assert component.model_id == "gpt-6.1-terra"
                assert component.effort == "medium"
            assert not container.conversations.runner.anthropic_caching


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="LLM_MODE"):
        build_container("unknown")


def test_jev_is_output_only_and_has_terra_medium_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("django.conf.settings.JEV_MODE", "synthetic")
    monkeypatch.setattr("django.conf.settings.AI_GATEWAY_API_KEY_FILE", "/private/test-key")
    monkeypatch.setattr(
        "screening.platform.container.build_openai_model",
        lambda role: ScriptedChatModel(responses=[]),
    )
    runner = build_container("openai").conversations.runner
    assert isinstance(runner, LangChainAgentRunner)
    assert isinstance(runner.guard, JevReplyGuard)
    assert runner.guard.synthetic_only
    assert runner.guard.key_file == "/private/test-key"
    assert runner.guard.fallback.model_id == "gpt-6.1-terra"
    assert runner.guard.fallback.effort == "medium"
    assert runner.full_policy_guard


def test_harness_hash_covers_agent_runtime_not_consoles() -> None:
    from pathlib import Path

    from adapters.eval_harness import runtime_files

    root = Path(__file__).resolve().parents[2]
    files = {str(p.relative_to(root)) for p in runtime_files(root)}
    assert "src/screening/application/prompts.py" in files
    assert "src/adapters/agent_langchain/runner.py" in files
    assert "uv.lock" in files
    assert not any(f.startswith(("src/web/conversations/", "src/worker/")) for f in files)
