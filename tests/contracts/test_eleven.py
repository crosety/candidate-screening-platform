import httpx
import pytest

from adapters.channels.eleven import ElevenAdapter, VoiceAgent


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [True, False])
@pytest.mark.parametrize("backup", ["disabled", "default", "override", "missing"])
async def test_private_agent_and_server_only_key(private: bool, backup: str) -> None:
    config = VoiceAgent(
        agent_id="demo",
        api_key="private-key",
        callback_secret="callback",
        callback_url="https://test.invalid/api/v1/voice/demo/v1/chat/completions",
    )
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == "private-key"
        paths.append(request.url.path)
        if request.url.path.endswith("/demo"):
            return httpx.Response(
                200,
                json={
                    "platform_settings": {
                        "auth": {"enable_auth": private},
                        "overrides": {"custom_llm_extra_body": True},
                    },
                    "conversation_config": {
                        "asr": {"user_input_audio_format": "pcm_16000"},
                        "tts": {"agent_output_audio_format": "pcm_16000"},
                        "agent": {
                            "first_message": "",
                            "prompt": {
                                "llm": "custom-llm",
                                "custom_llm": {
                                    "url": config.callback_url.removesuffix("/chat/completions")
                                },
                                **(
                                    {"backup_llm_config": {"preference": backup}}
                                    if backup != "missing"
                                    else {}
                                ),
                            },
                        },
                    },
                },
            )
        return httpx.Response(200, json={"signed_url": "wss://api.elevenlabs.io/test?token=short"})

    adapter = ElevenAdapter(config, httpx.MockTransport(respond))
    if private and backup == "disabled":
        assert (await adapter.signed_url()).startswith("wss://")
        assert len(paths) == 2
    else:
        with pytest.raises(ValueError, match="privado"):
            await adapter.signed_url()
        assert len(paths) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["audio", "network", "malformed"])
async def test_unusable_provider_fails_before_opening_a_conversation(invalid: str) -> None:
    config = VoiceAgent(
        agent_id="demo",
        api_key="private-key",
        callback_secret="callback",
        callback_url="https://test.invalid/callback",
    )
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if invalid == "network":
            raise httpx.ConnectTimeout("internal-details-must-not-leak", request=request)
        if invalid == "malformed":
            return httpx.Response(200, json={"conversation_config": None})
        return httpx.Response(
            200,
            json={
                "conversation_config": {
                    "asr": {"user_input_audio_format": "pcm_16000"},
                    "tts": {"agent_output_audio_format": "pcm_44100"},
                }
            },
        )

    with pytest.raises(ValueError) as error:
        await ElevenAdapter(config, httpx.MockTransport(respond)).signed_url()
    assert "private-key" not in str(error.value)
    assert "internal-details" not in str(error.value)
    assert paths == ["/v1/convai/agents/demo"]
