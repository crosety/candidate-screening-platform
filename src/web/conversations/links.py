"""Opaque candidate links: database compromise must not disclose usable bearer tokens."""

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone

if TYPE_CHECKING:
    from web.conversations.models import CandidateLink


def link_expiry() -> datetime:
    return timezone.now() + timedelta(days=30)


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def derived_token(link: "CandidateLink") -> str:
    material = f"candidate-link:{link.id}:{link.created_at.isoformat()}"
    return hmac.new(settings.SECRET_KEY.encode(), material.encode(), hashlib.sha256).hexdigest()


def visible_token(link: "CandidateLink") -> str | None:
    if link.revoked_at is not None or link.expires_at <= timezone.now():
        return None
    token = derived_token(link)
    # Existing links migrated to a digest cannot be recovered. They still authenticate,
    # but an operator must explicitly rotate them to issue a replacement.
    return token if hmac.compare_digest(link.token, digest_token(token)) else None
