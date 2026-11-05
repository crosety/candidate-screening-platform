from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from screening.domain.errors import NotFound
from screening.platform.container import get_container
from web.tenancy.models import Membership

router = Router()


class TenantOut(Schema):
    id: UUID
    slug: str
    name: str
    role: str
    active_version: str | None
    draft_rev: int


@router.get("/tenants", response=list[TenantOut])
def tenants(request: HttpRequest) -> list[TenantOut]:
    user_id = request.user.pk
    if user_id is None:
        raise HttpError(401, "Debes iniciar sesión")
    rows = (
        Membership.objects.filter(user_id=user_id).select_related("tenant").order_by("tenant__name")
    )
    service = get_container().profiles
    result: list[TenantOut] = []
    for row in rows:
        active = service.repo.active_publication(row.tenant_id)
        version = (
            service.repo.get_version(row.tenant_id, active.version_id).version if active else None
        )
        try:
            rev = service.get_draft(row.tenant_id).rev
        except NotFound:
            rev = 0
        result.append(
            TenantOut(
                id=row.tenant_id,
                slug=row.tenant.slug,
                name=row.tenant.name,
                role=row.role,
                active_version=version,
                draft_rev=rev,
            )
        )
    return result
