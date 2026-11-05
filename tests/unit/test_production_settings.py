import pytest
from django.core.exceptions import ImproperlyConfigured

from web.core.production import production_hosts


def valid() -> dict[str, str]:
    return {
        "DJANGO_ALLOWED_HOSTS": "app.example.test,chat.example.test",
        "DJANGO_SECRET_KEY": "synthetic-not-a-secret-" * 3,
        "INGEST_TOKEN": "synthetic-root-not-a-secret-" * 3,
        "CSRF_TRUSTED_ORIGINS": "https://app.example.test,https://chat.example.test",
        "CHAT_BASE_URL": "https://chat.example.test",
        "DATABASE_URL": "postgres://runtime:synthetic@db.example.test/app?sslmode=verify-full",
        "REDIS_URL": "rediss://user:synthetic@cache.example.test/0",
    }


def test_explicit_production_configuration() -> None:
    assert production_hosts(valid()) == ["app.example.test", "chat.example.test"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("DJANGO_ALLOWED_HOSTS", "*"),
        ("DJANGO_ALLOWED_HOSTS", ""),
        ("DJANGO_SECRET_KEY", "dev-secret"),
        ("INGEST_TOKEN", "dev-token"),
        ("DJANGO_DEBUG", "true"),
        ("SEED_PASSWORD", "seed-demo"),
        ("CSRF_TRUSTED_ORIGINS", "http://app.example.test"),
        ("CHAT_BASE_URL", "http://chat.example.test"),
        ("DATABASE_URL", "postgres://screening:screening@localhost/app"),
        ("REDIS_URL", "redis://localhost/0"),
    ],
)
def test_production_refuses_local_defaults(key: str, value: str) -> None:
    with pytest.raises(ImproperlyConfigured):
        production_hosts({**valid(), key: value})
