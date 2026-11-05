import asyncio
import atexit
from collections.abc import Callable, Coroutine
from functools import cache
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import close_old_connections

from screening.platform.container import get_container
from web.jobs.models import Job


@cache
def _async_runner() -> asyncio.Runner:
    # Cached model clients keep connections bound to the worker's event loop.
    return asyncio.Runner()


def close_async_runtime() -> None:
    if _async_runner.cache_info().currsize:
        _async_runner().close()
        _async_runner.cache_clear()


def _run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    async def run() -> T:
        # ORM calls run in the thread-sensitive executor, not the queue's main thread.
        await sync_to_async(close_old_connections, thread_sensitive=True)()
        try:
            return await coroutine
        finally:
            await sync_to_async(close_old_connections, thread_sensitive=True)()

    return _async_runner().run(run())


atexit.register(close_async_runtime)


def handle_noop(job: Job) -> None:
    pass


def outbox_dispatch(job: Job) -> None:
    if job.tenant_id is None:
        raise ValueError("El trabajo necesita un cliente")
    item_id = UUID(job.payload["outbox_item_id"])
    try:
        _run_async(get_container().outbox.dispatch(job.tenant_id, item_id))
    except Exception:
        if job.attempts >= job.max_attempts:
            _run_async(
                get_container().decisions.mark_outbox(
                    job.tenant_id, item_id, "dead", "Reintentos agotados"
                )
            )
        raise


HANDLERS: dict[str, Callable[[Job], None]] = {
    "noop": handle_noop,
    "outbox_dispatch": outbox_dispatch,
}


def langfuse_export(job: Job) -> None:
    from web.tracing.langfuse import export_llm_job

    export_llm_job(job)


HANDLERS["langfuse_export"] = langfuse_export


def eleven_metrics(job: Job) -> None:
    from web.tracing.langfuse import export_voice_job

    export_voice_job(job)


HANDLERS["eleven_metrics"] = eleven_metrics


def eval_run(job: Job) -> None:
    if job.tenant_id is None:
        raise ValueError("El trabajo necesita un cliente")
    _run_async(get_container().evals.execute(job.tenant_id, UUID(job.payload["run_id"])))


HANDLERS["eval_run"] = eval_run


def live_sync(job: Job) -> None:
    if job.tenant_id is None:
        raise ValueError("El trabajo necesita un cliente")
    _run_async(get_container().live.sync(job.tenant_id))


HANDLERS["live_sync"] = live_sync


def channel_inbound(job: Job) -> None:
    from datetime import datetime

    from django.conf import settings

    from adapters.channels.meta import MetaWhatsAppAdapter
    from adapters.persistence.channels import DjangoChannelStore
    from screening.application.channels import ChannelService
    from screening.domain.channels import NormalizedInbound, OutboundMessage
    from web.conversations.meta_webhook import sender_config

    if job.tenant_id is None:
        raise ValueError("El trabajo necesita un cliente")
    tenant_id = job.tenant_id
    if not settings.META_SEND_ENABLED:
        raise ValueError("Los envíos externos de Meta están desactivados")
    config = sender_config(tenant_id)
    container = get_container()
    adapter = MetaWhatsAppAdapter(config, container.clock, enabled=settings.META_SEND_ENABLED)
    service = ChannelService(
        container.conversations, DjangoChannelStore(), adapter, container.clock
    )
    from web.conversations.models import ChannelInbound
    from web.tenancy.context import tenant_context

    with tenant_context(tenant_id):
        data = ChannelInbound.objects.get(tenant_id=tenant_id, pk=job.payload["inbound_id"]).payload
    incoming = NormalizedInbound(
        tenant_id,
        "whatsapp",
        data["provider_message_id"],
        data["contact"],
        data["text"],
        datetime.fromisoformat(data["received_at"]),
        data.get("candidate_code"),
    )

    async def process() -> None:
        result = await service.receive(incoming)
        if result.status == "unknown_contact" and result.reply:
            await adapter.send(
                OutboundMessage(
                    tenant_id,
                    "whatsapp",
                    incoming.contact,
                    result.reply,
                    f"unknown:{incoming.provider_message_id}",
                )
            )

    # Do not auto-resend after ambiguous delivery or an interrupted model turn.
    # The durable job remains failed/dead for an explicit operator decision.
    _run_async(process())


HANDLERS["channel_inbound"] = channel_inbound


def twilio_inbound(job: Job) -> None:
    from web.conversations.twilio_runtime import receive

    if job.tenant_id is None:
        raise ValueError("Falta el cliente")
    _run_async(receive(job.tenant_id, UUID(job.payload["inbound_id"])))


def twilio_send(job: Job) -> None:
    from web.conversations.twilio_runtime import dispatch

    if job.tenant_id is None:
        raise ValueError("Falta el cliente")
    _run_async(dispatch(job.tenant_id, UUID(job.payload["outbound_id"])))


HANDLERS.update(twilio_inbound=twilio_inbound, twilio_send=twilio_send)


def telephone_dial(job: Job) -> None:
    from web.conversations.telephone import dispatch

    if job.tenant_id is None:
        raise ValueError("Falta el cliente")
    _run_async(dispatch(job.tenant_id, UUID(job.payload["attempt_id"])))


HANDLERS["telephone_dial"] = telephone_dial


def sms_link_send(job: Job) -> None:
    from web.conversations.sms_links import dispatch

    if job.tenant_id:
        _run_async(dispatch(job.tenant_id, UUID(job.payload["notification_id"])))


HANDLERS["sms_link_send"] = sms_link_send


def channel_first_contact(job: Job) -> None:
    from dataclasses import replace

    from django.conf import settings

    from adapters.channels.meta import MetaWhatsAppAdapter
    from adapters.persistence.channels import DjangoChannelStore
    from screening.application.channels import ChannelService
    from screening.application.followups import allowed_time
    from screening.domain.channels import ChannelTemplate, template_values
    from web.conversations.meta_webhook import sender_config
    from web.jobs.enqueue import enqueue

    if job.tenant_id is None or not settings.META_SEND_ENABLED:
        raise ValueError("El primer contacto externo no está habilitado")
    tenant_id = job.tenant_id
    config = sender_config(tenant_id)
    container = get_container()
    adapter = MetaWhatsAppAdapter(config, container.clock, enabled=True)
    service = ChannelService(
        container.conversations, DjangoChannelStore(), adapter, container.clock
    )

    async def process() -> None:
        app_id = UUID(job.payload["application_id"])
        app = await container.conversations.repo.get_application(tenant_id, app_id)
        until = allowed_time(container.clock.now(), app.timezone)
        if until > container.clock.now():
            await sync_to_async(enqueue)(
                "channel_first_contact",
                job.payload,
                tenant_id,
                f"first-contact:{tenant_id}:{app.id}:{until.isoformat()}",
                until,
                max_attempts=1,
            )
            return
        version = await container.conversations.versions.active(tenant_id)
        if version is None or version.content.channel_settings.whatsapp is None:
            raise ValueError("No hay configuración publicada de WhatsApp")
        channel = version.content.channel_settings.whatsapp
        item = next((t for t in channel.templates if t.purpose == "first_contact"), None)
        if item is None:
            raise ValueError("No hay plantilla de primer contacto")
        tenant = await container.conversations.repo.get_tenant(tenant_id)
        template = ChannelTemplate(
            item.name,
            item.language,
            "first_contact",
            template_values(
                item.variables, name=app.candidate_name, role=app.job_ref, company=tenant.name
            ),
        )
        template = replace(template, approved=await adapter.approved(template))
        await service.store.bind(tenant_id, app.phone, app.id)
        result = await service.proactive(tenant_id, app.phone, "", template)
        if result.status == "quiet_hours":
            until = allowed_time(container.clock.now(), app.timezone)
            await sync_to_async(enqueue)(
                "channel_first_contact",
                job.payload,
                tenant_id,
                f"first-contact:{tenant_id}:{app.id}:{until.isoformat()}",
                until,
                max_attempts=1,
            )
            return
        if result.status not in ("template", "opted_out"):
            raise ValueError(f"Primer contacto no enviado: {result.status}")

    _run_async(process())


HANDLERS["channel_first_contact"] = channel_first_contact


def followup_dispatch(job: Job) -> None:
    from django.conf import settings

    from adapters.channels.meta import MetaWhatsAppAdapter
    from adapters.persistence.channels import DjangoChannelStore
    from screening.application.channels import ChannelService
    from screening.application.followups import FollowupService
    from screening.application.ports import ChannelAdapter
    from web.conversations.meta_webhook import sender_config
    from web.conversations.models import ChannelIdentity
    from web.conversations.twilio_runtime import TwilioQueueAdapter
    from web.tenancy.context import tenant_context

    if job.tenant_id is None:
        raise ValueError("El recordatorio necesita un cliente")
    tenant_id = job.tenant_id
    service = get_container().followups
    followup_id = UUID(job.payload["followup_id"])
    row = _run_async(service.store.get(tenant_id, followup_id))
    if row.status != "scheduled":
        return
    if not row.simulated and row.channel == "whatsapp":
        # Each identity is sent by the provider it belongs to; the web needs no adapter.
        with tenant_context(tenant_id):
            provider = (
                ChannelIdentity.objects.filter(
                    tenant_id=tenant_id, channel="whatsapp", contact=row.contact
                )
                .values_list("provider", flat=True)
                .first()
            )
        container = get_container()
        adapter: ChannelAdapter
        if provider == "twilio":
            adapter = TwilioQueueAdapter()
        else:
            if not settings.META_SEND_ENABLED:
                raise ValueError("Los envíos externos de Meta están desactivados")
            adapter = MetaWhatsAppAdapter(sender_config(tenant_id), container.clock, enabled=True)
        channels = ChannelService(
            container.conversations, DjangoChannelStore(), adapter, container.clock
        )
        service = FollowupService(channels, service.store, service.web_push)
    _run_async(service.dispatch(tenant_id, followup_id))


HANDLERS["followup_dispatch"] = followup_dispatch


def followup_expire(job: Job) -> None:
    from web.conversations.models import ChannelIdentity, Event, Followup, Session
    from web.tenancy.context import tenant_context

    if job.tenant_id is None:
        raise ValueError("El cierre necesita cliente")
    tenant_id = job.tenant_id
    with tenant_context(tenant_id):
        session_id = Followup.objects.get(
            tenant_id=tenant_id, pk=job.payload["followup_id"]
        ).session_id
    stage = _run_async(get_container().reengagement.stage_for(tenant_id, session_id))

    @sync_to_async
    def expire_locked() -> None:
        with tenant_context(tenant_id):
            row = Followup.objects.select_for_update().get(
                tenant_id=tenant_id, pk=job.payload["followup_id"]
            )
            session = Session.objects.select_for_update().get(
                tenant_id=tenant_id, pk=row.session_id
            )
            identity = ChannelIdentity.objects.filter(
                tenant_id=tenant_id, contact=row.contact
            ).first()
            if (
                row.status != "sent"
                or session.status != "active"
                or identity is None
                or identity.last_inbound_at != row.last_inbound_at
            ):
                return
            session.status = "abandoned"
            session.ended_at = get_container().clock.now()
            session.abandoned_stage = stage
            session.save(update_fields=["status", "ended_at", "abandoned_stage"])
            Event.objects.create(
                tenant_id=tenant_id,
                session_id=session.id,
                type="followups_exhausted",
                payload={"followup_id": str(row.id), "stage": stage},
            )

    async def expire() -> None:
        async with get_container().conversations.turn_lock.hold(tenant_id, session_id):
            await expire_locked()

    _run_async(expire())


HANDLERS["followup_expire"] = followup_expire


def reengagement_tick(job: Job) -> None:
    _run_async(get_container().reengagement.tick())


HANDLERS["reengagement_tick"] = reengagement_tick


def maintenance(job: Job) -> None:
    from web.profiles.maintenance import execute

    if job.tenant_id is None:
        raise ValueError("La tarea necesita un cliente")
    _run_async(execute(job.tenant_id, UUID(job.payload["reservation_id"])))


HANDLERS["maintenance"] = maintenance


def faq_index(job: Job) -> None:
    from web.profiles.faq_api import context

    if job.tenant_id is None:
        raise ValueError("La indexación necesita cliente")
    version_id = UUID(job.payload["version_id"])
    ctx = _run_async(context(job.tenant_id, version_id))
    c = get_container()
    version = _run_async(c.conversations.versions.get(job.tenant_id, version_id))
    _run_async(c.faq.index(ctx, version.content))


HANDLERS["faq_index"] = faq_index


def candidate_snapshot(job: Job) -> None:
    from screening.application.snapshots import SnapshotService

    if job.tenant_id is None:
        raise ValueError("El resumen necesita un cliente")
    tenant_id = job.tenant_id
    c = get_container()

    async def capture() -> None:
        session = await c.conversations.repo.get_session(tenant_id, UUID(job.payload["session_id"]))
        service = SnapshotService(
            c.conversations.repo, c.conversations.versions, c.assessment.evaluator, c.clock
        )
        await service.capture(session.run_context)

    _run_async(capture())


HANDLERS["candidate_snapshot"] = candidate_snapshot


def interview_reminder(job: Job) -> None:
    """Deterministic reminder for a booked interview; stale versions are ignored."""
    if job.tenant_id is None:
        raise ValueError("El recordatorio de entrevista necesita un cliente")
    _run_async(
        get_container().interviews.remind(
            job.tenant_id, UUID(job.payload["booking_id"]), int(job.payload["sequence"])
        )
    )


HANDLERS["interview_reminder"] = interview_reminder
