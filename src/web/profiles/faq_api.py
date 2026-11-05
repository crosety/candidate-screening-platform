from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import Field

from screening.domain.run_context import RunContext
from screening.platform.container import get_container
from web.profiles.models import FaqChunk, ProfileVersion
from web.profiles.operation_schemas import FaqIndexOut, FaqSearchOut, FaqStatusOut
from web.tenancy.context import tenant_context
from web.tenancy.permissions import require_role

router = Router()


async def context(tenant_id: UUID, version_id: UUID) -> RunContext:
    tenant = await get_container().conversations.repo.get_tenant(tenant_id)
    await get_container().conversations.versions.get(tenant_id, version_id)
    return RunContext(
        "copiloto",
        tenant_id,
        tenant.slug,
        "tenant",
        "fde",
        None,
        None,
        version_id,
        frozenset({"faq.search"}),
        {},
    )


@router.get("/tenants/{tenant_id}/faq", response=list[FaqStatusOut])
def status(request: HttpRequest, tenant_id: UUID) -> list[dict[str, Any]]:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        return [
            {
                "version_id": str(v.id),
                "version": v.version,
                "fragments": FaqChunk.objects.filter(tenant_id=tenant_id, version=v).count(),
                "models": list(
                    FaqChunk.objects.filter(tenant_id=tenant_id, version=v)
                    .values_list("model_id", flat=True)
                    .distinct()
                ),
            }
            for v in ProfileVersion.objects.filter(tenant_id=tenant_id).order_by("-seq")
        ]


@router.post("/tenants/{tenant_id}/faq/{version_id}/index", response=FaqIndexOut)
async def index(request: HttpRequest, tenant_id: UUID, version_id: UUID) -> dict[str, int]:
    await sync_to_async(require_role)(request, tenant_id, {"admin", "fde"})
    c = get_container()
    ctx = await context(tenant_id, version_id)
    version = await c.conversations.versions.get(tenant_id, version_id)
    return {"fragments": await c.faq.index(ctx, version.content)}


class SearchInput(Schema):
    query: str = Field(min_length=1, max_length=500)


@router.post("/tenants/{tenant_id}/faq/{version_id}/search", response=FaqSearchOut)
async def search(
    request: HttpRequest, tenant_id: UUID, version_id: UUID, payload: SearchInput
) -> dict[str, Any]:
    await sync_to_async(require_role)(request, tenant_id, {"admin", "fde"})
    ctx = await context(tenant_id, version_id)
    return await get_container().faq.search(ctx, payload.query)
