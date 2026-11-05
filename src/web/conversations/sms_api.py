"""Explicit candidate/recruiter SMS-link requests, never campaign or arbitrary text APIs."""

from typing import Literal
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from web.conversations.links import digest_token
from web.conversations.models import CandidateLink, Session, SmsNotification
from web.conversations.sms_links import public_state, reserve_sms, revoke_sms, sms_configuration
from web.conversations.telephone_api import TelephoneStatusIn, resolve
from web.core.security import rate_limit
from web.tenancy.context import tenant_context
from web.tenancy.permissions import require_role
from web.tenancy.scope import require_session

router = Router()


class SmsRequest(Schema):
    request_id: UUID
    phone: str
    consent: bool


class CandidateSmsRequest(TelephoneStatusIn, SmsRequest):
    action: Literal["send", "revoke", "status"] = "send"


class SmsNotificationOut(Schema):
    id: str
    purpose: str
    state: str
    created_at: str
    revoked: bool
    segments: int


class SmsStatusOut(Schema):
    available: bool
    notifications: list[SmsNotificationOut]


class SmsRevokedOut(Schema):
    revoked: Literal[True]


@router.post(
    "/chat/sms-link", auth=None, response=SmsStatusOut | SmsNotificationOut | SmsRevokedOut
)
def candidate_sms(request: HttpRequest, payload: CandidateSmsRequest) -> dict[str, object]:
    rate_limit(request, "sms_link", 20)
    tenant = resolve(payload)
    if payload.action == "revoke":
        revoke_sms(tenant, payload.session_id)
        return {"revoked": True}
    if payload.action == "status":
        return status_for(tenant, payload.session_id)
    if payload.consent is not True:
        raise HttpError(
            400, "Debes autorizar el SMS con tu enlace y el aviso posterior a la llamada"
        )
    with tenant_context(tenant):
        link = CandidateLink.objects.get(tenant_id=tenant, token=digest_token(payload.token))
        return public_state(
            reserve_sms(
                tenant,
                payload.session_id,
                link.id,
                payload.phone.strip(),
                "invitation:" + str(payload.request_id),
            )
        )


def status_for(tenant: UUID, sid: UUID) -> dict[str, object]:
    from screening.domain.errors import NotAllowed

    try:
        sms_configuration(tenant)
        enabled = True
    except NotAllowed:
        enabled = False
    with tenant_context(tenant):
        return {
            "available": enabled,
            "notifications": [
                public_state(r)
                for r in SmsNotification.objects.filter(session_id=sid).order_by("created_at")
            ],
        }


@router.get("/tenants/{tenant_id}/sessions/{session_id}/sms-link", response=SmsStatusOut)
def staff_status(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> dict[str, object]:
    member = require_role(request, tenant_id, {"recruiter", "fde", "admin"})
    with tenant_context(tenant_id):
        require_session(member, session_id)
        return status_for(tenant_id, session_id)


@router.post("/tenants/{tenant_id}/sessions/{session_id}/sms-link", response=SmsNotificationOut)
def staff_sms(
    request: HttpRequest, tenant_id: UUID, session_id: UUID, payload: SmsRequest
) -> dict[str, object]:
    rate_limit(request, "sms_invite", 10)
    member = require_role(request, tenant_id, {"recruiter", "fde", "admin"})
    if payload.consent is not True:
        raise HttpError(400, "Confirma que el candidato ha solicitado estos avisos")
    with tenant_context(tenant_id):
        require_session(member, session_id)
        session = Session.objects.get(pk=session_id)
        link = (
            CandidateLink.objects.filter(
                tenant_id=tenant_id, application_id=session.application_id, revoked_at__isnull=True
            )
            .order_by("-created_at")
            .first()
        )
        if not link:
            raise HttpError(409, "La candidatura necesita un enlace personal vigente")
        return public_state(
            reserve_sms(
                tenant_id,
                session_id,
                link.id,
                payload.phone.strip(),
                "invitation:" + str(payload.request_id),
            )
        )
