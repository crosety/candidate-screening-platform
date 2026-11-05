from uuid import UUID

from django.db.models import QuerySet
from ninja.errors import HttpError

from web.conversations.models import Application, Session
from web.tenancy.models import Membership


def segments(membership: Membership) -> tuple[str, ...]:
    values = membership.allowed_segments
    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
        raise HttpError(403, "El alcance de tu cuenta necesita revisión.")
    return tuple(values)


def applications(membership: Membership) -> QuerySet[Application]:
    query = Application.objects.filter(tenant_id=membership.tenant_id)
    allowed = segments(membership)
    if membership.role in ("admin", "fde") or allowed == ("*",):
        return query
    return query.filter(segment__in=allowed)


def sessions(membership: Membership) -> QuerySet[Session]:
    return Session.objects.filter(
        tenant_id=membership.tenant_id, application_id__in=applications(membership).values("id")
    )


def require_session(membership: Membership, session_id: UUID) -> None:
    if not sessions(membership).filter(pk=session_id).exists():
        raise HttpError(404, "Conversación no encontrada")
