import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from worker import handlers
from worker.handlers import HANDLERS
from worker.loop import requeue_stale, run_once

pytestmark = pytest.mark.django_db(transaction=True)


def boom(job: Job) -> None:
    raise RuntimeError("Test handler failed")


def test_noop() -> None:
    job = enqueue("noop")
    assert run_once("test")
    job.refresh_from_db()
    assert job.status == "done"
    assert job.attempts == 1


def test_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(HANDLERS, "boom", boom)
    job = enqueue("boom")
    assert run_once("test")
    job.refresh_from_db()
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.run_at > timezone.now()
    assert job.last_error == "Test handler failed"


def test_dead_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(HANDLERS, "boom", boom)
    job = enqueue("boom")
    job.max_attempts = 1
    job.save()
    assert run_once("test")
    job.refresh_from_db()
    assert job.status == "dead"


def test_enqueue_is_idempotent() -> None:
    first = enqueue("noop", idempotency_key="same")
    second = enqueue("noop", idempotency_key="same")
    assert first.id == second.id
    assert Job.objects.count() == 1


def test_stale_job() -> None:
    job = enqueue("noop")
    job.status = "running"
    job.locked_at = timezone.now() - timedelta(minutes=11)
    job.save()
    requeue_stale(timezone.now())
    job.refresh_from_db()
    assert job.status == "queued"
    assert job.locked_at is None


def test_interrupted_non_repeatable_job_is_not_requeued() -> None:
    job = enqueue("channel_inbound", max_attempts=1)
    job.status = "running"
    job.attempts = 1
    job.locked_at = timezone.now() - timedelta(minutes=11)
    job.save()
    requeue_stale(timezone.now())
    job.refresh_from_db()
    assert job.status == "dead" and job.locked_at is None


def test_handler_runs_outside_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[bool] = []

    def inspect(job: Job) -> None:
        observed.append(connection.in_atomic_block)

    monkeypatch.setitem(HANDLERS, "inspect", inspect)
    enqueue("inspect")
    assert run_once("test")
    assert observed == [False]


def test_eval_jobs_reuse_a_live_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[asyncio.AbstractEventLoop] = []

    async def execute(tenant_id: object, run_id: object) -> None:
        if observed and observed[-1].is_closed():
            raise RuntimeError("Event loop is closed")
        observed.append(asyncio.get_running_loop())

    monkeypatch.setattr(
        handlers,
        "get_container",
        lambda: SimpleNamespace(evals=SimpleNamespace(execute=execute)),
    )
    job = Job(tenant_id=uuid4(), payload={"run_id": str(uuid4())})
    try:
        handlers.eval_run(job)
        handlers.eval_run(job)
        assert observed[0] is observed[1]
        assert not observed[0].is_closed()
    finally:
        handlers.close_async_runtime()
    assert observed[0].is_closed()
