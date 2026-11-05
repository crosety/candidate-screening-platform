from uuid import UUID

from django.http import HttpRequest
from ninja.errors import HttpError

from web.tenancy.models import Membership


def require_role(request: HttpRequest, tenant_id: UUID, roles: set[str]) -> Membership:
    user_id = request.user.pk
    if user_id is None:
        raise HttpError(404, "Cliente no encontrado")
    try:
        membership = Membership.objects.get(user_id=user_id, tenant_id=tenant_id)
    except Membership.DoesNotExist:
        raise HttpError(404, "Cliente no encontrado") from None
    if membership.role not in roles:
        raise HttpError(403, "No tienes permiso para esto")
    return membership
