import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest

from adapters.channels.eleven import ElevenAdapter, VoiceAgent, verify_speech_engine_jwt


def signed_jwt(key: str = "private-key", **claims: object) -> str:
    def encode(value: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    header = encode({"alg": "HS256"})
    payload = encode(
        {
            "iss": "https://api.elevenlabs.io/convai/speech-engine",
            "sub": "convai_speech_engine_upstream",
            "exp": time.time() + 60,
            "iat": time.time(),
            **claims,
        }
    )
    signature = hmac.digest(
        hashlib.sha256(key.encode()).digest(), f"{header}.{payload}".encode(), "sha256"
    )
    return f"{header}.{payload}." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 0},
        {"iss": "wrong"},
        {"sub": "wrong"},
        {"exp": "later"},
        {"exp": float("nan")},
        {"iat": None},
        {"iat": float("nan")},
        {"iat": time.time() + 3600},
    ],
)
def test_provider_auth_requires_valid_claims(claims: dict[str, object]) -> None:
    assert not verify_speech_engine_jwt(signed_jwt(**claims), "private-key")


def test_provider_auth_checks_signature_and_format() -> None:
    token = signed_jwt()
    assert verify_speech_engine_jwt(token, "private-key")
    assert verify_speech_engine_jwt("Bearer " + token, "private-key")
    assert verify_speech_engine_jwt(token, "private-key_residency_in")
    assert not verify_speech_engine_jwt(token, "another-key")
    for invalid in ["", "x.y.z", "a" * 8193]:
        assert not verify_speech_engine_jwt(invalid, "private-key")


@pytest.mark.parametrize("invalid", [None, "url", "session", "voice", "format", "greeting"])
async def test_engine_transport_is_bound_before_issuing_signed_url(invalid: str | None) -> None:
    config = VoiceAgent(
        agent_id="seng_test",
        api_key="private-key",
        callback_secret="unused",
        callback_url="https://callback.invalid/api/v1/voice/demo/v1/chat/completions",
    )
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == "private-key"
        paths.append(request.url.path)
        if request.url.path.endswith("seng_test"):
            return httpx.Response(
                200,
                json={
                    "speech_engine": {
                        "ws_url": "wss://wrong.invalid"
                        if invalid == "url"
                        else config.callback_url.replace("https:", "wss:"),
                        "request_headers": {
                            "X-Orbio-Session": {
                                "variable_name": "wrong"
                                if invalid == "session"
                                else "orbio_session_token"
                            }
                        },
                    },
                    "asr": {"user_input_audio_format": "pcm_16000"},
                    "tts": {
                        "voice_id": "wrong" if invalid == "voice" else "Sarah",
                        "agent_output_audio_format": "pcm_44100"
                        if invalid == "format"
                        else "pcm_16000",
                    },
                    "overrides": {"first_message": invalid == "greeting"},
                },
            )
        return httpx.Response(200, json={"signed_url": "wss://api.elevenlabs.io/test"})

    adapter = ElevenAdapter(config, httpx.MockTransport(respond), expected_voice_id="Sarah")
    if invalid:
        with pytest.raises(ValueError):
            await adapter.signed_url()
        assert len(paths) == 1
    else:
        assert await adapter.signed_url() == "wss://api.elevenlabs.io/test"
        assert len(paths) == 2
