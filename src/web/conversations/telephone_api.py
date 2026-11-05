"""Private candidate callback requests, never an arbitrary dialing endpoint."""

from uuid import UUID

from asgiref.sync import async_to_sync
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from screening.domain.errors import NotAllowed
from screening.platform.container import get_container
from web.conversations.links import digest_token
from web.conversations.models import Session, TelephoneAttempt
from web.conversations.telephone import (
    call_capacity,
    configuration,
    public_state,
    reserve,
    voice_ready,
)
from web.core.security import rate_limit
from web.tenancy.context import tenant_context

router = Router()


class TelephoneStatusIn(Schema):
    token: str
    session_id: UUID


class TelephoneRequestIn(TelephoneStatusIn):
    request_id: UUID
    phone: str
    consent: bool


class TelephoneAttemptOut(Schema):
    id: str
    state: str
    reason: str


class TelephoneStatusOut(Schema):
    available: bool
    reason: str
    sender: str
    attempt: TelephoneAttemptOut | None


def resolve(payload: TelephoneStatusIn) -> UUID:
    service = get_container().conversations
    target = async_to_sync(service.repo.resolve_link)(payload.token)
    session = async_to_sync(service.repo.get_session)(target.tenant_id, payload.session_id)
    if session.application_id != target.application_id:
        raise NotAllowed("Enlace ajeno a la candidatura")
    return target.tenant_id


@router.post("/chat/telephone/status", auth=None, response=TelephoneStatusOut)
def status(request: HttpRequest, payload: TelephoneStatusIn) -> dict[str, object]:
    rate_limit(request, "telephone_status", 90)
    tenant = resolve(payload)
    try:
        config = configuration(tenant)
        with tenant_context(tenant):
            app_id = Session.objects.get(pk=payload.session_id).application_id
        available = voice_ready(tenant, payload.session_id) and call_capacity(
            tenant, config, application_id=app_id
        )
        sender = config.sender
        reason = (
            ""
            if available
            else "Las llamadas no están disponibles ahora. Puedes continuar por escrito."
        )
    except NotAllowed:
        available, sender = False, ""
        reason = "Las llamadas no están disponibles ahora. Puedes continuar por escrito."
    with tenant_context(tenant):
        row = (
            TelephoneAttempt.objects.filter(session_id=payload.session_id)
            .order_by("-created_at")
            .first()
        )
        return {
            "available": available,
            "reason": reason,
            "sender": sender,
            "attempt": public_state(row) if row else None,
        }


@router.post("/chat/telephone/request", auth=None, response=TelephoneAttemptOut)
def request_call(request: HttpRequest, payload: TelephoneRequestIn) -> dict[str, str]:
    rate_limit(request, "telephone_request", 10)
    if payload.consent is not True:
        raise HttpError(400, "Debes solicitar y autorizar la llamada")
    tenant = resolve(payload)
    # No normalization of ambiguous destinations; only explicit Spanish mobile E.164.
    return public_state(
        reserve(
            tenant,
            payload.session_id,
            digest_token(payload.token),
            payload.request_id,
            payload.phone.strip(),
        )
    )
