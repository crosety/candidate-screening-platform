"""Tenant-specific HMAC credentials derived from a server-only root secret."""

import hashlib
import hmac
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from ninja.errors import HttpError


def tenant_key(root: str, tenant_slug: str) -> str:
    return hmac.new(root.encode(), f"ingest-v1:{tenant_slug}".encode(), hashlib.sha256).hexdigest()


def signature(key: str, timestamp: str, nonce: str, path: str, body: bytes) -> str:
    canonical = "\n".join([timestamp, nonce, "POST", path, hashlib.sha256(body).hexdigest()])
    return hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def authenticate_ingest(request: HttpRequest, tenant_slug: str) -> None:
    timestamp = request.headers.get("X-Ingest-Timestamp", "")
    nonce = request.headers.get("X-Ingest-Nonce", "")
    supplied = request.headers.get("X-Ingest-Signature", "")
    if (
        not settings.INGEST_TOKEN
        or not re.fullmatch(r"[0-9]{10}", timestamp)
        or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", nonce)
        or not re.fullmatch(r"[0-9a-f]{64}", supplied)
        or not -30 <= time.time() - int(timestamp) <= 300
    ):
        raise HttpError(401, "Firma de ingesta no válida o caducada")
    expected = signature(
        tenant_key(settings.INGEST_TOKEN, tenant_slug), timestamp, nonce, request.path, request.body
    )
    if not hmac.compare_digest(expected, supplied):
        raise HttpError(401, "Firma de ingesta no válida o caducada")
    try:
        fresh = cache.add(f"ingest:{tenant_slug}:{nonce}", True, 360)
    except Exception:
        raise HttpError(503, "No se puede verificar la repetición de la ingesta") from None
    if not fresh:
        raise HttpError(
            409, "Petición ya recibida; usa una nueva firma para consultar el resultado"
        )
