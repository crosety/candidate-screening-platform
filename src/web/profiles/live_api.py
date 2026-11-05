from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from asgiref.sync import async_to_sync
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from screening.platform.container import get_container
from web.jobs.enqueue import enqueue
from web.simulated.models import Site
from web.tenancy.context import tenant_context
from web.tenancy.permissions import require_role

router = Router()


class InspectOut(Schema):
    structure: list[str]


class ConnectorStatusOut(Schema):
    tenant_id: UUID
    connection_id: str
    summary: str
    warnings: list[dict[str, str]]
    checked_at: datetime


class SiteOut(Schema):
    external_id: str
    name: str


class OpenSiteIn(Schema):
    external_id: str = Field(min_length=1, max_length=60)


class OkOut(Schema):
    ok: bool


@router.get("/tenants/{tenant_id}/connections/{connection_id}/inspect", response=InspectOut)
def inspect_connection(request: HttpRequest, tenant_id: UUID, connection_id: str) -> InspectOut:
    require_role(request, tenant_id, {"admin", "fde"})
    container = get_container()
    connection = next(
        (
            c
            for c in container.profiles.get_draft(tenant_id).content.connections
            if c.id == connection_id
        ),
        None,
    )
    if connection is None:
        raise HttpError(404, "Conexión no encontrada")
    tenant = async_to_sync(container.live.tenants.get_tenant)(tenant_id)
    try:
        result = async_to_sync(container.live.registry.get(tenant.slug, connection).call)(
            "inspect", {}
        )
    except Exception:
        raise HttpError(400, "No se ha podido inspeccionar la conexión autorizada.") from None
    return InspectOut.model_validate(result)


@router.get("/tenants/{tenant_id}/live", response=list[ConnectorStatusOut])
def live(request: HttpRequest, tenant_id: UUID) -> list[ConnectorStatusOut]:
    require_role(request, tenant_id, {"admin", "fde"})
    container = get_container()
    now = container.clock.now()
    checked = async_to_sync(container.live.statuses.last_checked)(tenant_id)
    if checked is None or (now - checked).total_seconds() > 30:
        enqueue("live_sync", {}, tenant_id, f"live_sync:{tenant_id}:{int(now.timestamp()) // 30}")
    return [
        ConnectorStatusOut.model_validate(asdict(row))
        for row in async_to_sync(container.live.list_statuses)(tenant_id)
    ]


@router.get("/tenants/{tenant_id}/simulated/sites", response=list[SiteOut])
def sites(request: HttpRequest, tenant_id: UUID) -> list[SiteOut]:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        return [
            SiteOut(external_id=site.external_id, name=site.name)
            for site in Site.objects.filter(tenant_id=tenant_id).order_by("name")
        ]


@router.post("/tenants/{tenant_id}/simulated/open-site", response=OkOut)
def open_site(request: HttpRequest, tenant_id: UUID, payload: OpenSiteIn) -> OkOut:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        updated = Site.objects.filter(tenant_id=tenant_id, external_id=payload.external_id).update(
            vacancies={"morning": 0, "afternoon": 3, "night": 0, "rotating": 0}, has_calendar=False
        )
        if not updated:
            raise HttpError(404, "Local no encontrado")
    enqueue("live_sync", {}, tenant_id)
    return OkOut(ok=True)
