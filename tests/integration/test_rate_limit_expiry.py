"""Exercise the actual Redis boundary; never clear the application's cache."""

import hashlib
from uuid import uuid4

from django.core.cache import caches

from web.core.security import limited


def test_redis_admission_repairs_an_orphan_counter_without_resetting_it(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": settings.REDIS_URL,
            "KEY_PREFIX": "orbio-rate-test-" + uuid4().hex,
        }
    }
    backend = caches["default"]
    key = "limit:test:" + hashlib.sha256(b"candidate").hexdigest()
    client = backend._cache.get_client(key, write=True)
    raw_key = backend.make_key(key)
    try:
        backend.set(key, 20, timeout=None)
        assert limited("test", "candidate", 10, window=60)
        assert backend.get(key) == 21  # Repair expiry, not the admission count.
        assert 0 < client.ttl(raw_key) <= 60
        client.expire(raw_key, 30)
        assert limited("test", "candidate", 10, window=60)
        assert 0 < client.ttl(raw_key) <= 30  # Requests cannot slide the window.
        backend.delete(key)
        assert not limited("test", "candidate", 1, window=60)
        assert limited("test", "candidate", 1, window=60)
        assert 0 < client.ttl(raw_key) <= 60
    finally:
        backend.delete(key)
