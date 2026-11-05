from dataclasses import asdict
from uuid import UUID

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from screening.application.ports import DraftRecord, PublicationRecord, VersionRecord
from screening.platform.container import get_container
from web.accounts.models import User
from web.profiles.schemas import (
    ApprovalIn,
    CheckOut,
    DiffOut,
    DraftOut,
    PublicationIn,
    PublicationOut,
    RollbackIn,
    SaveDraftIn,
    VersionDetailOut,
    VersionOut,
)
from web.tenancy.permissions import require_role

router = Router()


def _actor(request: HttpRequest, tenant_id: UUID) -> UUID:
    require_role(request, tenant_id, {"admin", "fde"})
    user_id = request.user.pk
    if user_id is None:
        raise HttpError(401, "Debes iniciar sesión")
    return user_id


def _draft(record: DraftRecord) -> DraftOut:
    return DraftOut(rev=record.rev, content=record.content, base_version=record.base_version_id)


def _publication(tenant_id: UUID, record: PublicationRecord) -> PublicationOut:
    version = get_container().profiles.repo.get_version(tenant_id, record.version_id)
    return PublicationOut(
        id=record.id,
        version=version.version,
        state=record.state,
        percent=record.percent,
        needs_second_approval=record.needs_second_approval,
        needs_client_approval=record.needs_client_approval,
        approvals=record.approvals,
        segment=record.segment,
    )


def _version(tenant_id: UUID, record: VersionRecord) -> VersionOut:
    active = get_container().profiles.repo.active_publication(tenant_id)
    author = User.objects.get(pk=record.published_by_id)
    return VersionOut(
        id=record.id,
        version=record.version,
        seq=record.seq,
        risk=record.risk,
        note=record.note,
        published_by=author.full_name,
        created_at=record.created_at,
        active_percent=active.percent if active and active.version_id == record.id else None,
        can_rollback=get_container().profiles.repo.was_activated(tenant_id, record.id),
    )


@router.get("/tenants/{tenant_id}/draft", response=DraftOut)
def get_draft(request: HttpRequest, tenant_id: UUID) -> DraftOut:
    _actor(request, tenant_id)
    return _draft(get_container().profiles.get_draft(tenant_id))


@router.put("/tenants/{tenant_id}/draft", response=DraftOut)
def save_draft(request: HttpRequest, tenant_id: UUID, payload: SaveDraftIn) -> DraftOut:
    user_id = _actor(request, tenant_id)
    return _draft(
        get_container().profiles.save_draft(tenant_id, payload.rev, payload.content, user_id)
    )


@router.post("/tenants/{tenant_id}/draft/reset", response=DraftOut)
def reset_draft(request: HttpRequest, tenant_id: UUID) -> DraftOut:
    user_id = _actor(request, tenant_id)
    return _draft(get_container().profiles.reset_draft(tenant_id, user_id))


@router.get("/tenants/{tenant_id}/draft/checks", response=list[CheckOut])
def checks(request: HttpRequest, tenant_id: UUID) -> list[CheckOut]:
    _actor(request, tenant_id)
    return [CheckOut.model_validate(asdict(c)) for c in get_container().profiles.checks(tenant_id)]


@router.get("/tenants/{tenant_id}/draft/diff", response=DiffOut)
def diff(request: HttpRequest, tenant_id: UUID) -> DiffOut:
    _actor(request, tenant_id)
    return DiffOut.model_validate(asdict(get_container().profiles.diff(tenant_id)))


@router.get("/tenants/{tenant_id}/versions", response=list[VersionOut])
def versions(request: HttpRequest, tenant_id: UUID) -> list[VersionOut]:
    _actor(request, tenant_id)
    return [_version(tenant_id, v) for v in get_container().profiles.list_versions(tenant_id)]


@router.get("/tenants/{tenant_id}/versions/{version_id}", response=VersionDetailOut)
def version_detail(request: HttpRequest, tenant_id: UUID, version_id: UUID) -> VersionDetailOut:
    _actor(request, tenant_id)
    record = get_container().profiles.repo.get_version(tenant_id, version_id)
    return VersionDetailOut(**_version(tenant_id, record).model_dump(), content=record.content)


@router.post("/tenants/{tenant_id}/publications", response=PublicationOut)
def publish(request: HttpRequest, tenant_id: UUID, payload: PublicationIn) -> PublicationOut:
    user_id = _actor(request, tenant_id)
    return _publication(
        tenant_id,
        get_container().profiles.publish(
            tenant_id, payload.note, payload.percent, user_id, payload.segment
        ),
    )


@router.get("/tenants/{tenant_id}/publications", response=list[PublicationOut])
def publications(request: HttpRequest, tenant_id: UUID) -> list[PublicationOut]:
    _actor(request, tenant_id)
    return [
        _publication(tenant_id, p)
        for p in get_container().profiles.repo.list_publications(tenant_id)
        if p.state == "pending_approval"
    ]


@router.post("/tenants/{tenant_id}/publications/{publication_id}/cancel", response=PublicationOut)
def cancel_publication(
    request: HttpRequest, tenant_id: UUID, publication_id: UUID
) -> PublicationOut:
    _actor(request, tenant_id)
    return _publication(
        tenant_id, get_container().profiles.repo.cancel_publication(tenant_id, publication_id)
    )


@router.post(
    "/tenants/{tenant_id}/publications/{publication_id}/approvals", response=PublicationOut
)
def approve(
    request: HttpRequest, tenant_id: UUID, publication_id: UUID, payload: ApprovalIn
) -> PublicationOut:
    user_id = _actor(request, tenant_id)
    return _publication(
        tenant_id,
        get_container().profiles.approve(tenant_id, publication_id, payload.kind, user_id),
    )


@router.post("/tenants/{tenant_id}/versions/{version_id}/rollback", response=PublicationOut)
def rollback(
    request: HttpRequest, tenant_id: UUID, version_id: UUID, payload: RollbackIn
) -> PublicationOut:
    user_id = _actor(request, tenant_id)
    return _publication(
        tenant_id, get_container().profiles.rollback(tenant_id, version_id, payload.note, user_id)
    )
