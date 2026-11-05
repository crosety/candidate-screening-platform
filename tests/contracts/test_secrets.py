from pathlib import Path

import pytest

from adapters.secrets.env import EnvSecretStore
from screening.domain.errors import NotFound


def test_resolves_tenant_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_TEST_CLIENT_ATS_KEY", "example-value")
    assert EnvSecretStore().resolve("secret://test-client/ats-key") == "example-value"


@pytest.mark.parametrize("ref", ["file:///tmp/key", "secret://x", "secret://x/Y", "secret://a/b/c"])
def test_rejects_invalid_reference(ref: str) -> None:
    with pytest.raises(NotFound):
        EnvSecretStore().resolve(ref)


def test_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_TEST_CLIENT_MISSING", raising=False)
    with pytest.raises(NotFound):
        EnvSecretStore().resolve("secret://test-client/missing")


def test_resolves_private_file_without_inline_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "voice.json"
    secret.write_text('{"api_key":"test-only"}\n')
    monkeypatch.setenv("SECRET_TEST_CLIENT_VOICE_FILE", str(secret))
    assert EnvSecretStore().resolve("secret://test-client/voice") == '{"api_key":"test-only"}'


@pytest.mark.parametrize(
    "kind", ["missing", "directory", "relative", "oversized", "empty", "binary"]
)
def test_invalid_secret_file_fails_without_disclosing_path_or_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    secret = tmp_path / "private-file"
    if kind == "directory":
        secret.mkdir()
    elif kind == "oversized":
        secret.write_text("x" * 65_537)
    elif kind == "empty":
        secret.write_text("\n")
    elif kind == "binary":
        secret.write_bytes(b"\xff")
    monkeypatch.setenv(
        "SECRET_TEST_CLIENT_VOICE_FILE", "relative" if kind == "relative" else str(secret)
    )
    with pytest.raises(NotFound) as error:
        EnvSecretStore().resolve("secret://test-client/voice")
    assert str(tmp_path) not in str(error.value)


def test_ambiguous_secret_sources_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_TEST_CLIENT_VOICE", "test-only")
    monkeypatch.setenv("SECRET_TEST_CLIENT_VOICE_FILE", "/run/secrets/voice")
    with pytest.raises(NotFound, match="ambigua"):
        EnvSecretStore().resolve("secret://test-client/voice")
