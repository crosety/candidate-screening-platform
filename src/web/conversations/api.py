from dataclasses import asdict
from uuid import UUID

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db.models import Count, Sum
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from adapters.llm.langfuse import session_url
from screening.application.ports import NewApplication
from screening.platform.container import get_container
from web.conversations.ingest_auth import authenticate_ingest
from web.conversations.links import digest_token, visible_token
from web.conversations.meta_webhook import schedule_first_contact
from web.conversations.models import Application, CandidateLink, Message, Session
from web.conversations.schemas import (
    ApplicationOut,
    AtsRecordOut,
    CapabilityCallOut,
    ChatSessionIn,
    IngestOut,
    LlmCallOut,
    MessageOut,
    NewApplicationIn,
    NewAtsRecordIn,
    NewAtsRecordOut,
    OpenedSessionOut,
    SessionDetailOut,
    SessionListOut,
    SessionOut,
    SessionVersionOut,
)
from web.core.security import issue_socket_ticket, rate_limit
from web.decisions.models import Decision, Result
from web.simulated.models import AtsRecord
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant
from web.tenancy.permissions import require_role
from web.tracing.models import CapabilityCall, LlmCall

router = Router()


@router.post("/ingest/{tenant_slug}/applications", auth=None, response=IngestOut)
def ingest(request: HttpRequest, tenant_slug: str, payload: NewApplicationIn) -> IngestOut:
    authenticate_ingest(request, tenant_slug)
    application, link = async_to_sync(get_container().conversations.ingest)(
        tenant_slug, NewApplication(**payload.model_dump())
    )
    schedule_first_contact(application.tenant_id, application.id, application.phone)
    return IngestOut(application_id=application.id, candidate_link=link)


@router.post("/chat/sessions", auth=None, response=OpenedSessionOut)
def open_session(request: HttpRequest, payload: ChatSessionIn) -> OpenedSessionOut:
    rate_limit(request, "open_chat", 30)
    service = get_container().conversations
    opened = async_to_sync(service.open_session)(payload.token)
    session = async_to_sync(service.choose_language)(
        payload.token, opened.session_id, payload.language, payload.fallback_language
    )
    return OpenedSessionOut.model_validate(
        {
            **asdict(opened),
            "language": session.response_language,
            "websocket_token": issue_socket_ticket(
                session.tenant_id, opened.session_id, digest_token(payload.token)
            ),
        }
    )


@router.post("/tenants/{tenant_id}/simulated/ats", response=NewAtsRecordOut)
def create_ats_record(
    request: HttpRequest, tenant_id: UUID, payload: NewAtsRecordIn
) -> NewAtsRecordOut:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        # Recheck under the same tenant lock as publication: the selector may
        # have been loaded before another FDE paused or replaced the offer.
        profiles = get_container().profiles
        publication = profiles.repo.active_publication(tenant_id)
        # Session allocation may select the previous version during a pilot;
        # match public intake instead of creating a link with ambiguous context.
        if publication is None or publication.percent != 100:
            raise HttpError(
                409, "Espera a que la oferta esté publicada al 100 % antes de crear la prueba."
            )
        active = profiles.get_version(tenant_id, publication.version_id)
        if active.content.offers and not any(
            offer.key == payload.job_ref and offer.status == "open"
            for offer in active.content.offers
        ):
            raise HttpError(409, "Elige una oferta publicada y abierta antes de crear la prueba.")
        number = (
            AtsRecord.objects.filter(tenant_id=tenant_id, external_id__startswith="SIM-").count()
            + 1
        )
        external_id = f"SIM-{number:04d}"
        AtsRecord.objects.create(
            tenant_id=tenant_id, external_id=external_id, stage="Nuevo", **payload.model_dump()
        )
        _, link = async_to_sync(get_container().conversations.ingest)(
            tenant.slug,
            NewApplication(external_id=external_id, is_test=True, **payload.model_dump()),
        )
    return NewAtsRecordOut(external_id=external_id, candidate_link=link)


@router.get("/tenants/{tenant_id}/simulated/ats", response=list[AtsRecordOut])
def ats_records(request: HttpRequest, tenant_id: UUID) -> list[AtsRecordOut]:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        applications = dict(
            Application.objects.filter(tenant_id=tenant_id).values_list("external_id", "id")
        )
        links = {
            row.application_id: visible_token(row)
            for row in CandidateLink.objects.filter(
                tenant_id=tenant_id, application_id__in=applications.values()
            )
        }
        result = []
        for row in AtsRecord.objects.filter(tenant_id=tenant_id).order_by("-updated_at"):
            application_id = applications.get(row.external_id)
            token = links.get(application_id) if application_id is not None else None
            data = AtsRecordOut.model_validate(
                {
                    **{
                        field: getattr(row, field)
                        for field in (
                            "id",
                            "external_id",
                            "candidate_name",
                            "phone",
                            "city",
                            "job_ref",
                            "stage",
                            "fields",
                            "notes",
                            "updated_at",
                        )
                    },
                    "candidate_link": f"{settings.CHAT_BASE_URL}/c/{token}" if token else None,
                }
            )
            result.append(data)
        return result


@router.get("/tenants/{tenant_id}/sessions", response=list[SessionListOut])
def sessions(
    request: HttpRequest, tenant_id: UUID, status: str = "", limit: int = 50
) -> list[SessionListOut]:
    require_role(request, tenant_id, {"admin", "fde"})
    if not 1 <= limit <= 500:
        raise HttpError(400, "El límite debe estar entre 1 y 500.")
    with tenant_context(tenant_id):
        query = Session.objects.filter(tenant_id=tenant_id).select_related(
            "application", "profile_version"
        )
        if status:
            query = query.filter(status=status)
        counts = dict(
            Message.objects.filter(tenant_id=tenant_id)
            .values("session_id")
            .annotate(total=Count("id"))
            .values_list("session_id", "total")
        )
        costs = dict(
            LlmCall.objects.filter(tenant_id=tenant_id)
            .values("session_id")
            .annotate(total=Sum("cost_usd_micros"))
            .values_list("session_id", "total")
        )
        return [
            SessionListOut.model_validate(
                {
                    "id": row.id,
                    "candidate_name": row.application.candidate_name,
                    "status": row.status,
                    "version": row.profile_version.version,
                    "created_at": row.created_at,
                    "messages": counts.get(row.id, 0),
                    "cost_usd_micros": costs.get(row.id, 0) or 0,
                }
            )
            for row in query.order_by("-created_at")[:limit]
        ]


@router.get("/tenants/{tenant_id}/sessions/{session_id}", response=SessionDetailOut)
def session_detail(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> SessionDetailOut:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        try:
            row = Session.objects.select_related("application", "profile_version").get(
                tenant_id=tenant_id, pk=session_id
            )
        except Session.DoesNotExist:
            raise HttpError(404, "Conversación no encontrada") from None
        llm_calls = [
            LlmCallOut.model_validate(call, from_attributes=True)
            for call in LlmCall.objects.filter(session=row).order_by("created_at")
        ]
        return SessionDetailOut(
            langfuse_url=(
                session_url(
                    settings.LANGFUSE_HOST,
                    settings.LANGFUSE_PROJECT_ID,
                    f"{tenant_id}:{row.id}",
                )
                if row.application.erased_at is None
                else None
            ),
            session=SessionOut.model_validate(row, from_attributes=True),
            application=ApplicationOut.model_validate(row.application, from_attributes=True),
            version=SessionVersionOut.model_validate(row.profile_version, from_attributes=True),
            messages=[
                MessageOut.model_validate(message, from_attributes=True)
                for message in Message.objects.filter(session=row).order_by("created_at", "id")
            ],
            llm_calls=llm_calls,
            capability_calls=[
                CapabilityCallOut.model_validate(call, from_attributes=True)
                for call in CapabilityCall.objects.filter(session=row).order_by("created_at")
            ],
            result=(
                {
                    "verdict": result.verdict,
                    "fields": {k: v for k, v in result.fields.items() if k != "__reason"},
                    "reason": result.fields.get("__reason", ""),
                    "summary": result.summary,
                    "flags": result.flags,
                    "sentiment": result.sentiment,
                }
                if (result := Result.objects.filter(session=row).first())
                else None
            ),
            decisions=[
                dict(decision)
                for decision in (
                    Decision.objects.filter(session=row)
                    .order_by("created_at")
                    .values(
                        "id",
                        "decision_type",
                        "status",
                        "decided_by",
                        "user_id",
                        "reason",
                        "created_at",
                        "applied_at",
                    )
                )
            ],
            cost_usd_micros=sum(call.cost_usd_micros for call in llm_calls),
        )
