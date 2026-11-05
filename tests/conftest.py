from collections.abc import Iterator
from typing import Any

import pytest

from screening.platform.container import get_container


def redact_response(response: dict[str, Any]) -> dict[str, Any]:
    response["headers"] = {
        key: value
        for key, value in response["headers"].items()
        if key.lower() not in {"set-cookie", "openai-organization", "openai-project"}
    }
    return response


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return {
        "filter_headers": [
            "authorization",
            "api-key",
            "x-api-key",
            "x-amz-date",
            "x-amz-security-token",
            "x-amz-content-sha256",
            "amz-sdk-invocation-id",
            "amz-sdk-request",
        ],
        "match_on": ["method", "scheme", "host", "path", "body"],
        "decode_compressed_response": True,
        "before_record_response": redact_response,
    }


@pytest.fixture(autouse=True)
def quiet_cassette_logging(caplog: pytest.LogCaptureFixture) -> None:
    # VCR's INFO level serializes complete response bodies and headers.
    caplog.set_level("WARNING", logger="vcr")


@pytest.fixture(autouse=True)
def _fake_llm_and_fresh_container(settings: Any) -> Iterator[None]:
    from django.core.cache import cache

    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    settings.LLM_MODE = "fake"
    settings.LANGFUSE_ENABLED = False
    settings.LANGFUSE_CONFIG_FILE = ""
    settings.JEV_MODE = "off"
    settings.GPT_LIVE_ENABLED = False
    settings.PUBLISH_REQUIRES_EVALS = False
    settings.SEED_PASSWORD = "test-only-password"
    # Fast hashing only for synthetic test users; runtime keeps Django's PBKDF2.
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    get_container.cache_clear()
    yield
    get_container.cache_clear()
