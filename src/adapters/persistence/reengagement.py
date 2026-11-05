import hashlib
from datetime import datetime
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import Max, OuterRef, Subquery
from django.utils import timezone

from adapters.persistence.followups import _enqueue, cancel_pending
from screening.application.reengagement import SilentSession
from screening.domain.run_context import run_context_from_dict
from web.conversations.models import ChannelIdentity, Event, Followup, Message, Session
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

# Same hard floor as reminders requested by the agent (DjangoFollowupStore.create).
MAX_SENT_PER_APPLICATION = 3


def _last_inbound(tenant_id: UUID, session_id: UUID) -> datetime | None:
    value = Message.objects.filter(
        tenant_id=tenant_id, session_id=session_id, direction="in"
    ).aggregate(at=Max("created_at"))["at"]
    return value if isinstance(value, datetime) else None


class DjangoSilenceStore:
    @sync_to_async
    def silent_sessions(self) -> list[SilentSession]:
        result: list[SilentSession] = []
        for tenant_id in Tenant.objects.values_list("id", flat=True):
            with tenant_context(tenant_id):
                last = Message.objects.filter(session_id=OuterRef("pk")).order_by(
                    "-created_at", "-id"
                )
                rows = (
                    Session.objects.filter(
                        tenant_id=tenant_id,
                        status="active",
                        human_owner__isnull=True,
                        application__erased_at__isnull=True,
                        application__is_simulation=False,
                    )
                    .exclude(channel="sim")
                    # Already assessed: reopened only to change an interview, not to re-screen.
                    .exclude(result__isnull=False)
                    .exclude(
                        followup__status__in=("proposed", "scheduled"),
                    )
                    .annotate(
                        last_direction=Subquery(last.values("direction")[:1]),
                        last_at=Subquery(last.values("created_at")[:1]),
                    )
                    .filter(last_direction="out")
                    .select_related("application")
                )
                for session in rows:
                    last_in = _last_inbound(tenant_id, session.id)
                    sent = Followup.objects.filter(
                        tenant_id=tenant_id, session_id=session.id, status="sent"
                    )
                    if last_in is not None:
                        sent = sent.filter(run_at__gt=last_in)
                    identity = (
                        ChannelIdentity.objects.filter(
                            tenant_id=tenant_id,
                            application_id=session.application_id,
                            channel="whatsapp",
                            opted_out=False,
                        )
                        .order_by("-updated_at")
                        .first()
                    )
                    result.append(
                        SilentSession(
                            tenant_id=tenant_id,
                            session_id=session.id,
                            version_id=session.profile_version_id,
                            run_context=run_context_from_dict(session.run_context),
                            timezone=session.application.timezone,
                            last_out_at=session.last_at,
                            last_in_at=last_in,
                            sent=sent.count(),
                            last_reminder_at=sent.aggregate(at=Max("run_at"))["at"],
                            contact=identity.contact if identity else "",
                            simulated=bool(identity and identity.simulated),
                        )
                    )
        return result

    @sync_to_async
    def schedule(
        self, item: SilentSession, run_at: datetime, reason: str, status: str, key: str
    ) -> bool:
        digest = hashlib.sha256(key.encode()).hexdigest()
        with tenant_context(item.tenant_id):
            session = Session.objects.select_for_update().get(
                tenant_id=item.tenant_id, pk=item.session_id
            )
            followups = Followup.objects.filter(tenant_id=item.tenant_id, session=session)
            if (
                session.status != "active"
                or session.human_owner_id is not None
                or Followup.objects.filter(
                    tenant_id=item.tenant_id, idempotency_key=digest
                ).exists()
                or followups.filter(status__in=("proposed", "scheduled")).exists()
                or followups.filter(status="sent").count() >= MAX_SENT_PER_APPLICATION
                or _last_inbound(item.tenant_id, item.session_id) != item.last_in_at
            ):
                return False
            identity = (
                ChannelIdentity.objects.filter(
                    tenant_id=item.tenant_id, contact=item.contact, channel="whatsapp"
                ).first()
                if item.contact
                else None
            )
            row = Followup.objects.create(
                tenant_id=item.tenant_id,
                session=session,
                contact=item.contact,
                channel="whatsapp" if identity else "web",
                run_at=max(run_at, timezone.now()),
                reason=reason,
                status=status,
                simulated=item.simulated,
                # Dispatch cancels the reminder if this marker moved (the candidate wrote).
                last_inbound_at=identity.last_inbound_at if identity else item.last_in_at,
                idempotency_key=digest,
                author="platform",
            )
            Event.objects.create(
                tenant_id=item.tenant_id,
                session=session,
                type="silence_detected",
                payload={"followup_id": str(row.id), "reason": reason, "status": status},
            )
            if status == "scheduled":
                _enqueue(row)
            return True

    @sync_to_async
    def last_question(self, tenant_id: UUID, session_id: UUID) -> str:
        with tenant_context(tenant_id):
            last_in = _last_inbound(tenant_id, session_id)
            reminders = Followup.objects.filter(
                tenant_id=tenant_id, session_id=session_id, status="sent"
            )
            if last_in is not None:
                reminders = reminders.filter(run_at__gt=last_in)
            first = reminders.order_by("run_at").first()
            messages = Message.objects.filter(
                tenant_id=tenant_id, session_id=session_id, direction="out"
            )
            if first is not None:
                messages = messages.filter(created_at__lt=first.run_at)
            row = messages.order_by("-created_at", "-id").first()
            return row.text if row else ""

    @sync_to_async
    def abandon(
        self, tenant_id: UUID, session_id: UUID, last_in_at: datetime | None, stage: str
    ) -> bool:
        with tenant_context(tenant_id):
            session = Session.objects.select_for_update().get(tenant_id=tenant_id, pk=session_id)
            if (
                session.status != "active"
                or session.human_owner_id is not None
                or _last_inbound(tenant_id, session_id) != last_in_at
            ):
                return False
            session.status, session.ended_at = "abandoned", timezone.now()
            session.abandoned_stage = stage[:60]
            session.save(update_fields=["status", "ended_at", "abandoned_stage"])
            cancel_pending(tenant_id, session_id)
            Event.objects.create(
                tenant_id=tenant_id,
                session=session,
                type="followups_exhausted",
                payload={"stage": stage, "reason": "silence"},
            )
            return True
