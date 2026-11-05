"""Recruiter workbench: operational data by default; simulations are explicit."""

from typing import Any
from uuid import UUID

from django.conf import settings
from django.db.models import Count, Exists, F, Max, OuterRef, Q, QuerySet
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from screening.application.prompts import build_system_prompt
from screening.domain.fit import assess_fit
from screening.domain.profile import ProfileContent, active_sources, profile_for_offer
from screening.platform.container import get_container
from web.conversations.models import Event, Message, Offer, Session, TwilioOutbound
from web.conversations.workspace_schemas import (
    Environment,
    OfferPreviewOut,
    OffersOut,
    SaveOfferIn,
    WorkspaceDetail,
    WorkspaceOut,
)
from web.decisions.models import Decision, Result
from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership, Tenant
from web.tenancy.permissions import require_role
from web.tenancy.scope import applications, require_session, sessions

router = Router()


def selected_sessions(member: Membership, environment: Environment) -> QuerySet[Session]:
    return sessions(member).filter(
        application__erased_at__isnull=True, application__is_simulation=environment == "simulation"
    )


def session_row(row: Session) -> dict[str, Any]:
    app = row.application
    profile = ProfileContent.model_validate(row.profile_version.content)
    offer = next((o for o in profile.offers if o.key == row.run_context.get("offer_key")), None)
    last = row.message_set.order_by("-created_at", "-id").first()
    handoff = (
        row.event_set.filter(type__in=["handoff_requested", "auto_handoff", "provider_failed"])
        .order_by("-created_at")
        .first()
    )
    if handoff is None:
        handoff = row.event_set.filter(type="human_claim").order_by("-created_at").first()
    pending = row.decision_set.filter(status="proposed").exists()
    attention = (["human"] if row.status == "handed_off" else []) + (
        ["decision"] if pending else []
    )
    return {
        "id": str(row.id),
        "application_id": str(app.id),
        "name": app.candidate_name,
        "phone": app.phone,
        "offer_key": app.job_ref,
        "offer_title": offer.title if offer else app.job_ref,
        "status": row.status,
        "channel": row.channel,
        "received_at": app.created_at,
        "started_at": row.created_at,
        "last_interaction_at": last.created_at if last else row.created_at,
        "handed_off_at": handoff.created_at if handoff else None,
        "owner": row.human_owner.full_name if row.human_owner else None,
        "attention": attention,
        "next_step": "Responder al candidato"
        if row.status == "handed_off"
        else "Revisar evaluación"
        if pending
        else "Entrevista en curso"
        if row.status == "active"
        else "Conversación cerrada",
        "last_message": last.text[:240] if last else "Aún no hay mensajes.",
        "is_test": app.is_test,
        "is_simulation": app.is_simulation,
    }


@router.get("/tenants/{tenant_id}/workspace", response=WorkspaceOut)
def workspace(
    request: HttpRequest,
    tenant_id: UUID,
    environment: Environment = "production",
    offer: str = "",
    search: str = "",
    attention: bool = False,
    status: str = "",
    offset: int = 0,
) -> dict[str, Any]:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    if offset < 0 or status not in (
        "",
        "active",
        "handed_off",
        "completed",
        "abandoned",
        "awaiting",
    ):
        raise HttpError(400, "Filtros no válidos")
    with tenant_context(tenant_id):
        # One row per conversation, explicitly counted as such. Application totals stay distinct.
        base = selected_sessions(member, environment)
        pending = Decision.objects.filter(session_id=OuterRef("pk"), status="proposed")
        base = base.annotate(needs_decision=Exists(pending))
        needs = Q(status="handed_off") | Q(needs_decision=True)
        apps = applications(member).filter(
            erased_at__isnull=True, is_simulation=environment == "simulation"
        )
        if offer:
            base, apps = base.filter(application__job_ref=offer), apps.filter(job_ref=offer)
        counts = {
            "attention_count": base.filter(needs).count(),
            "active_count": base.filter(status="active").count(),
            "completed_count": base.filter(status__in=["completed", "abandoned"]).count(),
            "applications_count": apps.count(),
            "awaiting_start_count": apps.exclude(id__in=base.values("application_id")).count(),
        }
        if status == "awaiting":
            waiting = apps.exclude(id__in=base.values("application_id"))
            if attention:
                waiting = waiting.none()
            if search.strip():
                waiting = waiting.filter(
                    Q(candidate_name__icontains=search[:200]) | Q(phone__icontains=search[:200])
                )
            return {
                **counts,
                "total": waiting.count(),
                "rows": [],
                "awaiting": [
                    {
                        "id": str(app.id),
                        "name": app.candidate_name,
                        "phone": app.phone,
                        "offer_key": app.job_ref,
                        "received_at": app.created_at,
                    }
                    for app in waiting.order_by("-created_at", "id")[offset : offset + 40]
                ],
            }
        rows = base
        if attention:
            rows = rows.filter(needs)
        if status:
            rows = rows.filter(status=status)
        if search.strip():
            rows = rows.filter(
                Q(application__candidate_name__icontains=search[:200])
                | Q(application__phone__icontains=search[:200])
            )
        rows = rows.annotate(last_activity=Max("message__created_at")).order_by(
            F("last_activity").desc(nulls_last=True), "-created_at", "id"
        )
        return {
            **counts,
            "awaiting": [],
            "total": rows.count(),
            "rows": [
                session_row(r)
                for r in rows.select_related("application", "profile_version", "human_owner")[
                    offset : offset + 40
                ]
            ],
        }


@router.get("/tenants/{tenant_id}/workspace/{session_id}", response=WorkspaceDetail)
def detail(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> dict[str, Any]:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        require_session(member, session_id)
        row = (
            Session.objects.select_related("application", "profile_version", "human_owner")
            .filter(pk=session_id, application__erased_at__isnull=True)
            .first()
        )
        if row is None:
            raise HttpError(404, "No se ha encontrado esta candidatura")
        profile = ProfileContent.model_validate(row.profile_version.content)
        offer_key = row.run_context.get("offer_key", "")
        effective = profile_for_offer(profile, offer_key)
        offer = next((o for o in profile.offers if o.key == offer_key), None)
        result = Result.objects.filter(session=row).first()
        snapshot = (
            Event.objects.filter(session=row, type="candidate_snapshot")
            .order_by("-created_at", "-id")
            .first()
        )
        messages = list(Message.objects.filter(session=row).order_by("created_at", "id"))
        deliveries = dict(
            TwilioOutbound.objects.filter(message__session=row).values_list("message_id", "state")
        )
        fields = result.fields if result else snapshot.payload.get("fields", {}) if snapshot else {}
        # Result storage also contains internal metadata such as the string __reason.
        # Keep historical extraction keys, but never expose internal storage metadata.
        # Never present unsupported extraction as confirmed evidence, including older results.
        fields = {
            key: value
            if isinstance(value, dict)
            and isinstance(value.get("evidence"), str)
            and value["evidence"]
            and any(value["evidence"] in m.text for m in messages if m.direction == "in")
            else {"value": None, "evidence": None, "confidence": "low"}
            for key, value in fields.items()
            if not key.startswith("__")
        }
        handoff = (
            Event.objects.filter(
                session=row, type__in=["handoff_requested", "auto_handoff", "provider_failed"]
            )
            .order_by("-created_at")
            .first()
        )
        job = (
            Job.objects.filter(
                tenant_id=tenant_id, kind="candidate_snapshot", payload__session_id=str(session_id)
            )
            .order_by("-created_at")
            .first()
        )
        return {
            **session_row(row),
            "messages": [
                {
                    "id": str(m.id),
                    "direction": m.direction,
                    "text": m.text,
                    "channel": m.channel,
                    "created_at": m.created_at,
                    "delivery_status": deliveries.get(m.id, m.delivery_status),
                }
                for m in messages
            ],
            "fields": fields,
            "field_definitions": effective.result_schema,
            "summary": result.summary
            if result
            else snapshot.payload.get("summary", "")
            if snapshot
            else "",
            "provisional": result is None,
            "snapshot_at": result.created_at
            if result
            else snapshot.created_at
            if snapshot
            else None,
            "snapshot_stale": bool(
                snapshot and not result and snapshot.payload.get("message_count") != len(messages)
            ),
            "snapshot_status": job.status if job else "none",
            "fit": assess_fit(offer, fields),
            "handoff_reason": (
                "El asistente ha pedido que una persona revise el caso."
                if handoff and handoff.type != "handoff_requested"
                else str(handoff.payload.get("reason", ""))
                if handoff
                else ""
            ),
            "decisions": [
                {
                    "id": str(d.id),
                    "decision_type": d.decision_type,
                    "status": d.status,
                    "reason": d.reason,
                }
                for d in Decision.objects.filter(session=row).order_by("created_at")
            ],
            "version": row.profile_version.version,
            "offer_description": offer.description if offer else effective.brief.who,
        }


@router.post("/tenants/{tenant_id}/workspace/{session_id}/snapshot", response=dict[str, str])
def snapshot(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> dict[str, str]:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        require_session(member, session_id)
        row = Session.objects.get(pk=session_id)
        if row.application.erased_at or Result.objects.filter(session=row).exists():
            raise HttpError(409, "Esta conversación no necesita un resumen provisional")
        count = Message.objects.filter(session=row, direction="in").count()
        if not count:
            raise HttpError(400, "Aún no hay respuestas del candidato")
        job = enqueue(
            "candidate_snapshot",
            {"session_id": str(session_id)},
            tenant_id,
            f"snapshot:{session_id}:{count}",
            max_attempts=1,
        )
        if job.status in ("dead", "failed"):
            raise HttpError(
                409,
                "No se pudo generar el resumen. El equipo de configuración debe revisar "
                "el fallo antes de reintentarlo.",
            )
        return {"status": job.status}


@router.get("/tenants/{tenant_id}/offers", response=OffersOut)
def offers(request: HttpRequest, tenant_id: UUID) -> dict[str, Any]:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    service = get_container().profiles
    active = service.active_version(tenant_id)
    published = {o.key: o for o in active.content.offers} if active else {}
    draft = service.get_draft(tenant_id) if member.role in ("admin", "fde") else None
    draft_offers = {o.key: o for o in draft.content.offers} if draft else {}
    common_changes = bool(
        draft
        and (
            not active
            or draft.content.model_dump(exclude={"offers"})
            != active.content.model_dump(exclude={"offers"})
        )
    )
    with tenant_context(tenant_id):
        allowed_apps = applications(member).filter(erased_at__isnull=True, is_simulation=False)
        counts = {
            r["job_ref"]: r["n"] for r in allowed_apps.values("job_ref").annotate(n=Count("id"))
        }
        # Old references without configuration only matter while real candidates use them;
        # rows left by tests or simulations would show as empty "common configuration" cards.
        keys = set(published) | set(draft_offers) | set(counts)
        output = []
        tenant = Tenant.objects.get(pk=tenant_id)
        for key in sorted(keys):
            live, pending = published.get(key), draft_offers.get(key)
            item = live or pending
            attention = (
                selected_sessions(member, "production")
                .filter(application__job_ref=key)
                .filter(Q(status="handed_off") | Q(decision__status="proposed"))
                .distinct()
                .count()
            )
            output.append(
                {
                    "key": key,
                    "title": item.title if item else key,
                    "status": live.status if live else "draft" if pending else "legacy",
                    "description": item.description
                    if item
                    else "Usa la configuración común publicada del cliente.",
                    "dimensions": item.dimensions if item else {},
                    "applications": counts.get(key, 0),
                    "attention": attention,
                    "configured": bool(item),
                    "draft": pending,
                    "published": live if member.role in ("admin", "fde") else None,
                    "has_changes": bool(draft and (pending != live or common_changes)),
                    "application_url": f"{settings.CHAT_BASE_URL}/apply/{tenant.slug}/{key}"
                    if live and live.status == "open"
                    else None,
                }
            )
        return {
            "offers": output,
            "rev": draft.rev if draft else None,
            "shared_fields": draft.content.result_schema if draft else [],
            "allowed_capabilities": draft.content.grants.conversador if draft else [],
            "sources": draft.content.sources if draft else [],
            "common_changes": common_changes,
            "schema_version": draft.content.schema_version if draft else 2,
        }


@router.post("/tenants/{tenant_id}/offers/preview", response=OfferPreviewOut)
def preview_offer(request: HttpRequest, tenant_id: UUID, payload: SaveOfferIn) -> dict[str, Any]:
    require_role(request, tenant_id, {"admin", "fde"})
    draft = get_container().profiles.get_draft(tenant_id)
    if draft.rev != payload.rev:
        raise HttpError(409, "El borrador ha cambiado. Recarga antes de previsualizar.")
    data = draft.content.model_dump()
    definitions = {o.key: o for o in draft.content.offers}
    definitions[payload.definition.key] = payload.definition
    data["offers"] = [o.model_dump() for o in definitions.values()]
    profile = ProfileContent.model_validate(data)
    effective = profile_for_offer(profile, payload.definition.key)
    return {
        "fields": effective.result_schema,
        "fields_origin": payload.definition.fields_mode
        or ("custom" if payload.definition.fields else "inherit"),
        "sources": active_sources(effective),
        "capabilities": effective.grants.conversador,
        "prompt": build_system_prompt(effective, Tenant.objects.get(pk=tenant_id).name, "web"),
        "affected_offers": [o.title for o in profile.offers],
    }


@router.put("/tenants/{tenant_id}/offers/{offer_key}", response=OffersOut)
def save_offer(
    request: HttpRequest, tenant_id: UUID, offer_key: str, payload: SaveOfferIn
) -> dict[str, Any]:
    member = require_role(request, tenant_id, {"admin", "fde"})
    if payload.definition.key != offer_key:
        raise HttpError(400, "La referencia de la oferta no puede cambiar")
    service = get_container().profiles
    draft = service.get_draft(tenant_id)
    content = draft.content.model_dump()
    definitions = {o.key: o for o in draft.content.offers}
    definitions[offer_key] = payload.definition
    content["offers"] = [o.model_dump() for o in definitions.values()]
    with tenant_context(tenant_id):
        # Creation must not silently act as an update when a stable key is reused.
        # The shared draft revision also rejects concurrent creations/edits.
        if request.headers.get("If-None-Match") == "*" and (
            any(o.key == offer_key for o in draft.content.offers)
            or Offer.objects.filter(tenant_id=tenant_id, key=offer_key).exists()
        ):
            raise HttpError(
                412, "Ya existe una oferta con esta referencia. Usa otra o edita la existente."
            )
        service.save_draft(
            tenant_id, payload.rev, ProfileContent.model_validate(content), member.user_id
        )
        Offer.objects.get_or_create(tenant_id=tenant_id, key=offer_key)
    return offers(request, tenant_id)
