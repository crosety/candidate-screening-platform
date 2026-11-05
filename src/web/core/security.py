"""Bound abuse without recording credentials, bearer URLs or message text."""

import hashlib
import secrets
from typing import Any, cast
from uuid import UUID

from django.core.cache import cache, caches
from django.core.cache.backends.redis import RedisCache
from django.http import HttpRequest
from ninja.errors import HttpError


def limited(scope: str, identity: str, limit: int, window: int = 60) -> bool:
    key = "limit:" + scope + ":" + hashlib.sha256(identity.encode()).hexdigest()
    try:
        backend = caches["default"]
        if isinstance(backend, RedisCache):
            # Django's EXISTS + INCR can recreate an expired key without a TTL.
            # Keep increment and expiry atomic, also repairing legacy orphan counters.
            # This private client boundary is covered against the pinned Django + Redis.
            client = cast(Any, backend)._cache.get_client(key, write=True)
            count = client.eval(
                "local n = redis.call('INCR', KEYS[1]); "
                "if redis.call('TTL', KEYS[1]) < 0 then "
                "redis.call('EXPIRE', KEYS[1], ARGV[1]); end; return n",
                1,
                backend.make_key(key),
                window,
            )
            return int(count) > limit
        if cache.add(key, 1, window):
            return False
        return int(cache.incr(key)) > limit
    except ValueError:
        return not cache.add(key, 1, window)
    except Exception:
        return True  # Cache failure must not turn off the public boundary.


def rate_limit(request: HttpRequest, scope: str, limit: int, identity: str = "") -> None:
    address = str(request.META.get("REMOTE_ADDR", "unknown"))
    if limited(scope, address + ":" + identity, limit):
        raise HttpError(429, "Demasiados intentos. Espera un minuto antes de volver a intentarlo.")


def issue_socket_ticket(tenant_id: UUID, session_id: UUID, token_digest: str) -> str:
    ticket = secrets.token_urlsafe(32)
    cache.set(
        "socket:" + hashlib.sha256(ticket.encode()).hexdigest(),
        {
            "tenant_id": str(tenant_id),
            "session_id": str(session_id),
            "token_digest": token_digest,
        },
        60,
    )
    return ticket


def consume_socket_ticket(ticket: str, session_id: UUID) -> dict[str, Any] | None:
    key = "socket:" + hashlib.sha256(ticket.encode()).hexdigest()
    # add is atomic in Redis (and in the test cache): only one connection can claim it.
    if not cache.add(key + ":used", True, 60):
        return None
    payload = cache.get(key)
    cache.delete(key)
    if not isinstance(payload, dict) or payload.get("session_id") != str(session_id):
        return None
    return payload
