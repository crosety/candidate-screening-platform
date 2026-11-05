from pathlib import Path


def test_containers_do_not_inherit_host_credentials_or_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text()
    assert "env_file:" not in compose
    assert "/.aws" not in compose
    assert "- .:/app" not in compose
    assert "OPENAI_API_KEY:" not in compose
    assert "OPENAI_API_KEY_FILE: /run/secrets/openai_api_key" in compose
    assert compose.count("secrets: [openai_api_key]") == 2
    assert 'DJANGO_DEBUG: "false"' in compose
    ignored = (root / ".dockerignore").read_text().splitlines()
    assert {".env", ".env.*", ".runtime-secrets", ".local"} <= set(ignored)


def test_build_context_excludes_private_working_notes_and_generated_evidence() -> None:
    root = Path(__file__).resolve().parents[2]
    ignored = set((root / ".dockerignore").read_text().splitlines())
    assert {".scratch", ".agents", ".claude", "output", "tmp", "*.log"} <= ignored
