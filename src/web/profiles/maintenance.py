from dataclasses import replace
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async

from adapters.llm.budget import UsageCounter, current_usage
from adapters.privacy import pseudonymize
from screening.domain.evals import Persona
from screening.domain.run_context import RunContext
from screening.platform.container import get_container
from web.conversations.models import Session
from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from web.profiles.models import BudgetReservation, MaintenancePolicy
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent, Tenant


def reserve(
    tenant_id: UUID, kind: str, key: str, now: datetime, session_id: UUID | None = None
) -> UUID | None:
    with tenant_context(tenant_id):
        policy = MaintenancePolicy.objects.select_for_update().filter(tenant_id=tenant_id).first()
        if (
            policy is None
            or (kind == "nightly" and not policy.nightly_enabled)
            or (kind == "judge" and not policy.judge_percent)
            or (kind == "improvement" and not policy.improvement_enabled)
        ):
            return None
        if BudgetReservation.objects.filter(tenant_id=tenant_id, key=key).exists():
            return None
        day = now.astimezone(ZoneInfo(policy.timezone)).date()
        rows = BudgetReservation.objects.filter(tenant_id=tenant_id, day=day).exclude(
            state="cancelled"
        )
        used = sum(
            r.reserved_micros if r.state in ("reserved", "running") else r.actual_micros
            for r in rows
        )
        amount = policy.run_budget_micros
        if used + amount > policy.daily_budget_micros:
            return None
        row = BudgetReservation.objects.create(
            tenant_id=tenant_id,
            key=key,
            day=day,
            kind=kind,
            reserved_micros=amount,
            session_id=session_id,
        )
        job = enqueue(
            "maintenance",
            {"reservation_id": str(row.id)},
            tenant_id,
            f"maintenance:{row.id}",
            max_attempts=1,
        )
        row.job_id = job.id
        row.save(update_fields=["job_id"])
        return row.id


def tick(now: datetime) -> None:
    for tenant_id in Tenant.objects.values_list("id", flat=True):
        with tenant_context(tenant_id):
            policy = MaintenancePolicy.objects.filter(tenant_id=tenant_id).first()
            if policy is None:
                continue
            local = now.astimezone(ZoneInfo(policy.timezone))
            if policy.nightly_enabled and local.hour >= policy.nightly_hour:
                reserve(tenant_id, "nightly", f"nightly:{local.date()}", now)
            if policy.improvement_enabled and local.hour >= policy.nightly_hour:
                reserve(tenant_id, "improvement", f"improvement:{local.date()}", now)
            if policy.judge_percent:
                for session in Session.objects.filter(
                    tenant_id=tenant_id, status="completed"
                ).order_by("-created_at")[:50]:
                    if int(session.id.hex[:8], 16) % 100 < policy.judge_percent:
                        reserve(tenant_id, "judge", f"judge:{session.id}", now, session.id)


async def execute(tenant_id: UUID, reservation_id: UUID) -> None:
    @sync_to_async
    def claim() -> tuple[str, int, UUID, UUID | None] | None:
        with tenant_context(tenant_id):
            row = BudgetReservation.objects.select_for_update().get(
                tenant_id=tenant_id, pk=reservation_id
            )
            if row.state != "reserved":
                return None
            policy = MaintenancePolicy.objects.get(tenant_id=tenant_id)
            if (
                (row.kind == "improvement" and not policy.improvement_enabled)
                or (row.kind == "nightly" and not policy.nightly_enabled)
                or (row.kind == "judge" and not policy.judge_percent)
            ):
                row.state = "cancelled"
                row.save(update_fields=["state"])
                return None
            row.state = "running"
            row.save(update_fields=["state"])
            return row.kind, row.reserved_micros, policy.updated_by_id, row.session_id

    item = await claim()
    if item is None:
        return
    kind, amount, user, session_id = item
    c = get_container()

    counter = UsageCounter()
    usage_token = current_usage.set(counter)
    state = "failed"
    try:
        if kind == "nightly":
            active = await c.conversations.versions.active(tenant_id)
            if active is None:
                raise ValueError("No hay versión publicada para evaluar")
            run = await c.evals.start(
                tenant_id,
                None,
                False,
                amount / 1_000_000,
                user,
                enqueue=False,
                alternative=active.content,
            )
            await c.evals.execute(tenant_id, run.id)
        elif kind == "improvement":
            from web.profiles.improvement import propose

            await propose(tenant_id, user, max_cost_usd=amount / 1_000_000, execute=True)
        elif session_id is not None:
            session = await c.conversations.repo.get_session(tenant_id, session_id)
            version = await c.conversations.versions.get(tenant_id, session.profile_version_id)
            ctx = RunContext(
                "juez",
                tenant_id,
                session.run_context.tenant_slug,
                "platform",
                "nobody",
                session_id,
                session.application_id,
                version.id,
                frozenset(),
                {},
            )
            persona = Persona(
                key="quality_sample",
                name="Candidato seudonimizado",
                facts="Muestra real: no hay veredicto esperado externo.",
                style="",
                expected_verdict="review",
            )
            app = await c.conversations.repo.get_application(tenant_id, session.application_id)
            messages = [
                replace(m, text=pseudonymize(m.text, app.candidate_name, app.phone))
                for m in await c.conversations.repo.list_messages(tenant_id, session_id)
            ]
            judged = await c.evals.judge.judge(
                ctx,
                version.content,
                persona,
                messages,
                None,
            )

            @sync_to_async
            def record() -> None:
                with tenant_context(tenant_id):
                    AuditEvent.objects.create(
                        tenant_id=tenant_id,
                        action="quality.sample",
                        payload={
                            "session_id": str(session_id),
                            "judge": judged.model_dump(),
                            "selection_decision": False,
                        },
                    )

            await record()
        state = "done"
    finally:
        actual = counter.micros
        current_usage.reset(usage_token)

        @sync_to_async
        def finish() -> None:
            with tenant_context(tenant_id):
                BudgetReservation.objects.filter(tenant_id=tenant_id, pk=reservation_id).update(
                    state="over_budget" if actual > amount else state, actual_micros=actual
                )

        await finish()


def cancel(tenant_id: UUID, reservation_id: UUID) -> None:
    with tenant_context(tenant_id):
        row = BudgetReservation.objects.select_for_update().get(
            tenant_id=tenant_id, pk=reservation_id
        )
        if row.state == "reserved":
            row.state = "cancelled"
            row.save(update_fields=["state"])
            if row.job_id is not None:
                Job.objects.filter(pk=row.job_id, status="queued", tenant_id=tenant_id).update(
                    status="cancelled"
                )
