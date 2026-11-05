import logging
import signal
import time
from datetime import datetime, timedelta
from types import FrameType

from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from worker.handlers import HANDLERS, close_async_runtime

logger = logging.getLogger(__name__)


def claim_one(worker_id: str) -> Job | None:
    now = timezone.now()
    with transaction.atomic():
        job = (
            Job.objects.select_for_update(skip_locked=True)
            .filter(status="queued", run_at__lte=now)
            .order_by("run_at")
            .first()
        )
        if job is None:
            return None
        job.status = "running"
        job.locked_by = worker_id
        job.locked_at = now
        job.attempts += 1
        job.save(update_fields=["status", "locked_by", "locked_at", "attempts", "updated_at"])
        return job


def run_once(worker_id: str) -> bool:
    job = claim_one(worker_id)
    if job is None:
        return False
    try:
        HANDLERS[job.kind](job)
    except Exception as exc:
        job.last_error = str(exc)
        job.status = "queued" if job.attempts < job.max_attempts else "dead"
        if job.status == "queued":
            job.run_at = timezone.now() + timedelta(seconds=min(2**job.attempts, 300))
        logger.warning("Job %s (%s) ended with status %s", job.id, job.kind, job.status)
    else:
        job.status = "done"
        job.last_error = ""
    job.locked_by = ""
    job.locked_at = None
    job.save(
        update_fields=["status", "last_error", "run_at", "locked_by", "locked_at", "updated_at"]
    )
    return True


def requeue_stale(now: datetime) -> None:
    stale = Job.objects.filter(status="running", locked_at__lt=now - timedelta(minutes=10))
    stale.filter(attempts__gte=F("max_attempts")).update(
        status="dead",
        locked_by="",
        locked_at=None,
        last_error="Trabajo interrumpido; revisión manual para evitar repetir efectos.",
        updated_at=now,
    )
    stale.filter(attempts__lt=F("max_attempts")).update(
        status="queued", locked_by="", locked_at=None, run_at=now, updated_at=now
    )


def run_forever(worker_id: str, poll_seconds: float = 1.0) -> None:
    stopping = False

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    last_recovery = 0.0
    try:
        while not stopping:
            close_old_connections()
            now = time.monotonic()
            if now - last_recovery >= 60:
                from web.decisions.reconcile import reconcile_outbox
                from web.profiles.maintenance import tick

                tick(timezone.now())
                # One silence scan per minute across workers; the key deduplicates it.
                minute = timezone.now().replace(second=0, microsecond=0)
                enqueue(
                    "reengagement_tick",
                    {},
                    None,
                    f"reengagement:{minute.isoformat()}",
                    minute,
                    max_attempts=1,
                )
                requeue_stale(timezone.now())
                reconcile_outbox()
                last_recovery = now
            if not run_once(worker_id):
                time.sleep(poll_seconds)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        close_async_runtime()
        close_old_connections()
