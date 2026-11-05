"""Fail-closed settings gate; not an infrastructure deployment or compliance claim."""

from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import ImproperlyConfigured


def production_hosts(values: Mapping[str, str]) -> list[str]:
    hosts = [s.strip() for s in values.get("DJANGO_ALLOWED_HOSTS", "").split(",") if s.strip()]
    if not hosts or any("*" in h or h in {"localhost", "0.0.0.0", "127.0.0.1"} for h in hosts):
        raise ImproperlyConfigured("Producción necesita DJANGO_ALLOWED_HOSTS explícitos.")
    for name in ("DJANGO_SECRET_KEY", "INGEST_TOKEN"):
        value = values.get(name, "")
        if len(value) < 32 or value.startswith(("dev-", "change", "example")):
            raise ImproperlyConfigured(f"Producción necesita {name} privado, no un ejemplo.")
    if values.get("DJANGO_DEBUG", "false").lower() not in {"false", "0", "no"}:
        raise ImproperlyConfigured("DEBUG no está permitido en producción.")
    if values.get("SEED_PASSWORD"):
        raise ImproperlyConfigured("No se permiten credenciales de semillas en producción.")
    origins = values.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if not origins or any(urlsplit(s.strip()).scheme != "https" for s in origins):
        raise ImproperlyConfigured("Los orígenes CSRF de producción deben usar HTTPS.")
    if urlsplit(values.get("CHAT_BASE_URL", "")).scheme != "https":
        raise ImproperlyConfigured("CHAT_BASE_URL debe usar HTTPS en producción.")
    database = urlsplit(values.get("DATABASE_URL", ""))
    if (
        not database.password
        or database.password in {"postgres", "screening", "password"}
        or parse_qs(database.query).get("sslmode") != ["verify-full"]
    ):
        raise ImproperlyConfigured("Postgres requiere credenciales privadas y TLS verify-full.")
    redis = urlsplit(values.get("REDIS_URL", ""))
    if redis.scheme != "rediss" or not redis.password:
        raise ImproperlyConfigured("Redis requiere TLS y autenticación en producción.")
    return hosts
