import redis
from django.conf import settings
from django.db import connection
from django.http import HttpRequest
from ninja import Router

router = Router()


@router.get("/health", auth=None, response={200: dict[str, str], 503: dict[str, str]})
def health(request: HttpRequest) -> tuple[int, dict[str, str]]:
    result = {"status": "ok", "db": "ok", "redis": "ok"}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        result["db"] = "error"
    try:
        with redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1) as client:
            client.ping()
    except Exception:
        result["redis"] = "error"
    if result["db"] == "error" or result["redis"] == "error":
        result["status"] = "error"
        return 503, result
    return 200, result
