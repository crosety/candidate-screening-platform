import logging.config
import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "api"]
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS", default=["http://localhost:5173", "http://localhost:5174"]
)

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "channels",
    "web.core",
    "web.accounts",
    "web.tenancy",
    "web.jobs",
    "web.profiles",
    "web.conversations",
    "web.tracing",
    "web.simulated",
    "web.decisions",
    "web.evals",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "web.core.accesslog.RedactedAccessLog",
]

ROOT_URLCONF = "web.urls"
ASGI_APPLICATION = "web.asgi.application"
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 0
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

REDIS_URL = env("REDIS_URL")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "orbio-security",
    }
}
MCP_ALLOWED_URLS = env.list(
    "MCP_ALLOWED_URLS", default=["http://mcp-registro:8100/mcp", "http://localhost:8100/mcp"]
)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        # Channels uses a five-second blocking pop; redis-py's socket deadline
        # must not race that server-side timeout and tear down idle WebSockets.
        "CONFIG": {"hosts": [{"address": REDIS_URL, "socket_timeout": None}]},
    }
}

LANGUAGE_CODE = "es"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False

if env.bool("DJANGO_PRODUCTION", default=False):
    from web.core.production import production_hosts

    ALLOWED_HOSTS = production_hosts(os.environ)
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_AGE = 8 * 60 * 60
    SESSION_EXPIRE_AT_BROWSER_CLOSE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_CONTENT_TYPE_NOSNIFF = True

AWS_REGION = env("AWS_REGION", default="eu-west-1")
LLM_MODE = env("LLM_MODE", default="fake")
LANGFUSE_ENABLED = env.bool("LANGFUSE_ENABLED", default=False)
LANGFUSE_PROJECT_ID = env("LANGFUSE_PROJECT_ID", default="")
LANGFUSE_HOST = env("LANGFUSE_HOST", default="https://cloud.langfuse.com")
LANGFUSE_CONFIG_FILE = env("LANGFUSE_CONFIG_FILE", default="")
LANGFUSE_ENVIRONMENT = env("LANGFUSE_ENVIRONMENT", default="local")
INGEST_TOKEN = env("INGEST_TOKEN", default="")
CHAT_BASE_URL = env("CHAT_BASE_URL", default="http://localhost:5174")
SEEDS_DIR = BASE_DIR / env("SEEDS_DIR", default="seeds")
PUBLISH_REQUIRES_EVALS = env.bool("PUBLISH_REQUIRES_EVALS", default=True)
SEED_PASSWORD = env("SEED_PASSWORD", default="")
META_SEND_ENABLED = env.bool("META_SEND_ENABLED", default=False)
TWILIO_CONFIG_FILE = env("TWILIO_CONFIG_FILE", default="")
TWILIO_VOICE_CONFIG_FILE = env("TWILIO_VOICE_CONFIG_FILE", default="")
TWILIO_SMS_CONFIG_FILE = env("TWILIO_SMS_CONFIG_FILE", default="")
CANDIDATE_DIST_DIR = env("CANDIDATE_DIST_DIR", default="apps/chat/dist")
JEV_MODE = env("JEV_MODE", default="off")
GPT_LIVE_ENABLED = env.bool("GPT_LIVE_ENABLED", default=False)
ELEVEN_DIRECT_AGENTS_FILE = env("ELEVEN_DIRECT_AGENTS_FILE", default="")
ELEVENLABS_API_KEY_FILE = env("ELEVENLABS_API_KEY_FILE", default="")
ELEVEN_DIRECT_DAILY_LIMIT = env.int("ELEVEN_DIRECT_DAILY_LIMIT", default=20)
GPT_LIVE_VOICE_ID = env("GPT_LIVE_VOICE_ID", default="")
AI_GATEWAY_API_KEY_FILE = env("AI_GATEWAY_API_KEY_FILE", default="")
if JEV_MODE not in {"off", "synthetic", "zdr"}:
    raise ValueError("JEV_MODE debe ser off, synthetic o zdr")

# Docker mounts the OpenAI key only in API/worker, not in inspectable env metadata.
if key_file := env("OPENAI_API_KEY_FILE", default=""):
    os.environ["OPENAI_API_KEY"] = Path(key_file).read_text().strip()

LOGGING_CONFIG = None
logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
        "root": {"handlers": ["console"], "level": "INFO"},
    }
)
