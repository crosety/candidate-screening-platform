"""Reusable public offer entry points, separate from private candidate resume links."""

import hashlib
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from screening.application.prompts import build_system_prompt
from screening.domain.profile import OfferDefinition, ProfileContent, profile_for_offer
from screening.domain.run_context import RunContext, run_context_to_dict
from web.conversations.links import derived_token, digest_token, visible_token
from web.conversations.models import Application, CandidateLink, Event, Offer, Session
from web.core.security import rate_limit
from web.profiles.models import ProfileVersion, Publication
from web.simulated.models import AtsRecord
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

router = Router()


class PublicOffer(Schema):
    company: str
    title: str
    description: str
    dimensions: dict[str, str]


class ApplyInput(Schema):
    request_id: UUID
    name: str = Field(min_length=2, max_length=200)
    phone: str = Field(default="", max_length=30, pattern=r"^[+0-9 ()-]*$")
    consent: bool


class ApplyOutput(Schema):
    candidate_link: str
    session_id: UUID


def published_offer(
    tenant: Tenant, key: str
) -> tuple[ProfileVersion, ProfileContent, OfferDefinition]:
    publication = (
        Publication.objects.filter(tenant=tenant, state="active", segment="")
        .select_related("version")
        .order_by("-created_at")
        .first()
    )
    if publication is None:
        raise HttpError(404, "No se ha encontrado esta oferta")
    profile = ProfileContent.model_validate(publication.version.content)
    offer = next((o for o in profile.offers if o.key == key), None)
    if offer is None:
        raise HttpError(404, "No se ha encontrado esta oferta")
    if offer.status != "open":
        raise HttpError(410, "Esta oferta ya no admite candidaturas nuevas")
    # Public intake requires an unambiguous full rollout. Existing private chats are unaffected.
    if publication.percent != 100:
        raise HttpError(409, "Esta oferta se está actualizando. Inténtalo más tarde")
    return publication.version, profile, offer


def public_tenant(slug: str) -> Tenant:
    tenant = Tenant.objects.filter(slug=slug).first()
    if tenant is None:
        raise HttpError(404, "No se ha encontrado esta oferta")
    return tenant


@router.get("/apply/{tenant_slug}/{offer_key}", auth=None, response=PublicOffer)
def offer_detail(request: HttpRequest, tenant_slug: str, offer_key: str) -> dict[str, Any]:
    rate_limit(request, "view_offer", 60)
    tenant = public_tenant(tenant_slug)
    with tenant_context(tenant.id):
        _, _, offer = published_offer(tenant, offer_key)
        return {
            "company": tenant.name,
            "title": offer.title,
            "description": offer.description,
            "dimensions": offer.dimensions,
        }


@router.post("/apply/{tenant_slug}/{offer_key}", auth=None, response=ApplyOutput)
def apply(
    request: HttpRequest, tenant_slug: str, offer_key: str, payload: ApplyInput
) -> dict[str, Any]:
    rate_limit(request, "apply_offer", 15)
    if not payload.consent or len(payload.name.strip()) < 2:
        raise HttpError(400, "Indica tu nombre y revisa el aviso antes de continuar")
    tenant = public_tenant(tenant_slug)
    return create_application(tenant, offer_key, payload)


def create_application(
    tenant: Tenant, offer_key: str, payload: ApplyInput, channel: str = "web"
) -> dict[str, Any]:
    """Shared intake after transport-specific consent and identity validation."""
    if not payload.consent or len(payload.name.strip()) < 2 or channel not in {"web", "whatsapp"}:
        raise HttpError(400, "Indica tu nombre y revisa el aviso antes de continuar")
    external_id = (
        channel.upper()
        + "-"
        + hashlib.sha256(f"{offer_key}:{payload.request_id}".encode()).hexdigest()[:48]
    )
    with tenant_context(tenant.id):
        # Serialize duplicate requests without creating two applications or changing their facts.
        Tenant.objects.select_for_update().get(pk=tenant.id)
        version, profile, definition = published_offer(tenant, offer_key)
        existing = Application.objects.filter(tenant=tenant, external_id=external_id).first()
        if existing:
            if existing.erased_at or (existing.candidate_name, existing.phone) != (
                payload.name.strip(),
                payload.phone.strip(),
            ):
                raise HttpError(
                    409, "Esta solicitud ya se ha utilizado. Abre de nuevo el enlace de la oferta"
                )
            link = CandidateLink.objects.get(tenant=tenant, application_id=existing.id)
            token = visible_token(link)
            if token is None:
                raise HttpError(410, "El enlace de esta candidatura ya no está disponible")
            session = Session.objects.get(application=existing)
            return {
                "candidate_link": f"{settings.CHAT_BASE_URL}/c/{token}",
                "session_id": session.id,
            }
        offer, _ = Offer.objects.get_or_create(tenant=tenant, key=offer_key)
        # A loaded fictional fixture owns its offers, whichever version later publishes them.
        fixture = (
            Event.objects.filter(
                type="workspace_example_loaded", payload__offer_keys__contains=[offer_key]
            )
            .order_by("-created_at")
            .first()
        )
        app = Application.objects.create(
            tenant=tenant,
            external_id=external_id,
            candidate_name=payload.name.strip(),
            phone=payload.phone.strip(),
            city="",
            job_ref=offer_key,
            offer=offer,
            is_test=fixture is not None,
            segment=fixture.payload["key"] if fixture else "",
            is_simulation=False,
        )
        # Only the local ATS is materialized here. Never schedule an external first contact.
        if any(c.settings.get("driver") == "sim_ats" for c in profile.connections):
            AtsRecord.objects.create(
                tenant=tenant,
                external_id=external_id,
                candidate_name=app.candidate_name,
                phone=app.phone,
                city="",
                job_ref=offer_key,
                stage="Nuevo",
            )
        effective = profile_for_offer(profile, definition.key)
        session = Session(
            tenant=tenant,
            application=app,
            profile_version=version,
            channel=channel,
            status="active",
        )
        ctx = RunContext(
            agent="conversador",
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            acts_for="tenant",
            talks_to="candidate",
            session_id=session.id,
            application_id=app.id,
            profile_version_id=version.id,
            granted=frozenset(effective.grants.conversador),
            autonomy=effective.autonomy.model_dump(),
            offer_key=offer_key,
        )
        session.run_context = run_context_to_dict(ctx)
        session.system_prompt = build_system_prompt(effective, tenant.name, channel)
        session.save()
        link = CandidateLink(tenant=tenant, application_id=app.id)
        link.token = digest_token(str(link.id))
        link.save()
        token = derived_token(link)
        link.token = digest_token(token)
        link.save(update_fields=["token"])
        Event.objects.create(
            tenant=tenant,
            session=session,
            type="application_started",
            payload={
                "offer_key": offer_key,
                "notice_version": "2026-10-27",
                "consent": True,
                "channel": channel,
            },
        )
        return {"candidate_link": f"{settings.CHAT_BASE_URL}/c/{token}", "session_id": session.id}
