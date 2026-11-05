import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from adapters.persistence.tracing import DjangoTraceSink
from screening.application.ports import LlmUsage
from tests.api.test_conversations_api import conversation_client, open_session
from tests.api.test_phone_api import phone
from web.jobs.models import Job
from web.tenancy.models import Tenant
from worker.loop import run_once

__all__ = ["conversation_client", "phone"]

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def export_config(settings: Any, tmp_path: Path) -> None:
    path = tmp_path / "langfuse.json"
    path.write_text(
        json.dumps(
            {
                "host": "https://cloud.langfuse.com",
                "project_id": "test-project",
                "public_key": "test-public",
                "secret_key": "test-secret",
            }
        )
    )
    settings.LANGFUSE_ENABLED = True
    settings.LANGFUSE_PROJECT_ID = "test-project"
    settings.LANGFUSE_CONFIG_FILE = str(path)
    settings.LANGFUSE_ENVIRONMENT = "test"
    settings.LANGFUSE_HOST = "https://cloud.langfuse.com"


def queue_metric(agent: str = "recruiter") -> Job:
    tenant = Tenant.objects.create(slug=f"metric_{uuid4().hex[:8]}", name="Never export this name")
    usage = LlmUsage(
        tenant.id, None, None, agent, "gpt-6.1-terra", "medium", 100, 20, 30, 5, 270, 42, "stop"
    )
    async_to_sync(DjangoTraceSink().record_llm_call)(usage)
    return Job.objects.get(tenant=tenant, kind="langfuse_export")


def test_worker_exports_otlp_metrics_and_explicit_cost_without_personal_data(
    export_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/public/projects":
            return httpx.Response(200, json={"data": [{"id": "test-project"}]})
        if request.url.path == "/api/public/v2/observations":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    job = queue_metric()
    assert run_once("metrics-test")
    job.refresh_from_db()
    assert job.status == "done"
    request = requests[-1]
    assert request.url.path == "/api/public/otel/v1/traces"
    assert request.headers["x-langfuse-ingestion-version"] == "4"
    span = json.loads(request.content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in span["attributes"]}
    assert attrs["langfuse.observation.model.name"] == "gpt-6.1-terra"
    assert json.loads(attrs["langfuse.observation.usage_details"]) == {
        "input": 100,
        "output": 20,
        "input_cache_read": 30,
        "input_cache_write": 5,
    }
    assert json.loads(attrs["langfuse.observation.cost_details"]) == {"total": 0.000270}
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 42_000_000
    assert attrs["langfuse.observation.metadata.agent"] == "recruiter"
    assert (
        attrs["langfuse.session.id"]
        == hashlib.sha256(f"{job.tenant_id}:{job.payload['call_id']}".encode()).hexdigest()
    )
    assert "Never export" not in request.content.decode()
    assert not any(key.endswith((".input", ".output")) for key in attrs)


def test_fde_link_matches_the_session_actually_exported_by_the_worker(
    conversation_client: tuple[Client, UUID],
    export_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/projects":
            return httpx.Response(200, json={"data": [{"id": "test-project"}]})
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        spans.extend(json.loads(request.content)["resourceSpans"][0]["scopeSpans"][0]["spans"])
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    client, tenant = conversation_client
    session = UUID(open_session(client))
    async_to_sync(DjangoTraceSink().record_llm_call)(
        LlmUsage(
            tenant, session, None, "conversador", "test", "low", 10, 20, 0, 0, 750, 100, "stop"
        )
    )
    assert run_once("session-link-contract")
    assert len(spans) == 1
    exported_session = next(
        attribute["value"]["stringValue"]
        for attribute in spans[0]["attributes"]
        if attribute["key"] == "langfuse.session.id"
    )
    url = client.get(f"/api/v1/tenants/{tenant}/sessions/{session}").json()["langfuse_url"]
    assert url == f"https://cloud.langfuse.com/project/test-project/sessions/{exported_session}"


@pytest.mark.parametrize(
    "failure", ["timeout", "partial", "wrong_project", "redirect", "wrong_region"]
)
def test_delivery_failures_are_retryable_private_and_never_change_the_target(
    export_config: None,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    settings: Any,
) -> None:
    posts: list[bytes] = []
    if failure == "wrong_region":
        settings.LANGFUSE_HOST = "https://us.cloud.langfuse.com"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/projects":
            project = "wrong" if failure == "wrong_project" else "test-project"
            return httpx.Response(200, json={"data": [{"id": project}]})
        if request.url.path == "/api/public/v2/observations":
            return httpx.Response(200, json={"data": []})
        posts.append(request.content)
        if failure == "timeout":
            raise httpx.ReadTimeout("private provider detail test-secret")
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://untrusted.invalid"})
        return httpx.Response(
            200,
            json={
                "partialSuccess": {
                    "rejectedSpans": 1,
                    "errorMessage": "private provider detail test-secret",
                }
            },
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    job = queue_metric()
    run_once("failure-test")
    job.refresh_from_db()
    assert job.status == "queued"
    assert "test-secret" not in job.last_error
    if failure in {"wrong_project", "wrong_region"}:
        assert not posts
    else:
        # An ambiguous POST may already have arrived. Never repeat it blindly.
        from django.utils import timezone

        job.run_at = timezone.now()
        job.save(update_fields=["run_at"])
        run_once("failure-test")
        assert len(posts) == 1
        job.refresh_from_db()
        assert job.payload["delivery"] == "uncertain"


def test_job_cannot_export_another_tenants_trace(
    export_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbid_network(*args: Any, **kwargs: Any) -> httpx.Response:
        pytest.fail("Tenant isolation must fail before any HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    job = queue_metric()
    other = Tenant.objects.create(slug="other_metrics", name="Other")
    job.tenant = other
    job.save(update_fields=["tenant"])
    run_once("isolation-test")
    job.refresh_from_db()
    assert job.status == "queued"
    assert "does not exist" in job.last_error


def test_an_acknowledged_delivery_does_not_repeat_its_post(
    export_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.utils import timezone

    posts: list[bytes] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/projects":
            return httpx.Response(200, json={"data": [{"id": "test-project"}]})
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        posts.append(request.content)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    job = queue_metric()
    run_once("delivery-replay")
    job.refresh_from_db()
    assert job.payload["delivery"] == "delivered"
    job.status = "queued"
    job.run_at = timezone.now()
    job.save()
    run_once("delivery-replay")
    job.refresh_from_db()
    assert job.status == "done" and len(posts) == 1


@pytest.mark.parametrize(
    "role",
    [
        "conversador",
        "guardian",
        "evaluador",
        "juez",
        "simulador",
        "copiloto",
        "senales",
        "recruiter",
        "jev",
        "embedding",
        "gpt_live",
    ],
)
def test_all_model_roles_use_the_same_export_path(
    export_config: None,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    observed: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/v2/observations":
            return httpx.Response(200, json={"data": []})
        if request.method == "POST":
            observed.append(request.content.decode())
        return httpx.Response(200, json={"data": [{"id": "test-project"}]})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    queue_metric(role)
    run_once("roles-test")
    assert len(observed) == 1
    assert f"orbio.{role}" in observed[0]


def test_gpt_live_unknown_tokens_are_not_exported_as_zero(
    export_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[bytes] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/v2/observations":
            return httpx.Response(200, json={"data": []})
        if request.method == "POST":
            observed.append(request.content)
        return httpx.Response(200, json={"data": [{"id": "test-project"}]})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    tenant = Tenant.objects.create(slug="gpt_live_metrics", name="Synthetic voice")
    async_to_sync(DjangoTraceSink().record_llm_call)(
        LlmUsage(
            tenant.id, None, None, "voice", "gpt-live", "live", 0, 0, 0, 0, 800, 4000, "finalized"
        )
    )
    run_once("gpt-live-metrics")
    span = json.loads(observed[0])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in span["attributes"]}
    assert "langfuse.observation.usage_details" not in attrs
    assert attrs["langfuse.observation.metadata.timing"] == "whole_call"
    assert json.loads(attrs["langfuse.observation.cost_details"]) == {"total": 0.0008}


def test_reconciliation_is_preview_by_default_and_idempotent(export_config: None) -> None:
    from django.core.management import call_command

    job = queue_metric()
    options = {"tenant": job.tenant.slug, "since": "2026-01-01T00:00:00Z"}
    job.status = "dead"
    job.attempts = 10
    job.save()
    call_command("export_metrics", **options, retry=True)
    job.refresh_from_db()
    assert job.status == "dead"
    call_command("export_metrics", **options, retry=True, apply=True)
    call_command("export_metrics", **options, apply=True)
    job.refresh_from_db()
    assert job.status == "queued" and job.attempts == 0
    assert Job.objects.filter(kind="langfuse_export").count() == 1


def test_export_config_errors_do_not_disclose_private_material(tmp_path: Path) -> None:
    from adapters.llm.langfuse import LangfuseConfig, MetricsExportError

    path = tmp_path / "invalid.json"
    for host in (
        "http://cloud.langfuse.com",
        "https://other.invalid",
        "https://cloud.langfuse.com.evil",
    ):
        path.write_text(
            json.dumps(
                {
                    "host": host,
                    "project_id": "p",
                    "public_key": "sensitive",
                    "secret_key": "sensitive",
                }
            )
        )
        with pytest.raises(MetricsExportError, match="no válida") as exc:
            LangfuseConfig.load(str(path), "p")
        assert "sensitive" not in str(exc.value)


def test_voice_worker_retries_processing_then_exports_only_sanitized_stable_metrics(
    export_config: None, phone: Any, settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.utils import timezone

    from screening.platform.container import get_container
    from web.tracing.langfuse import enqueue_voice_metrics

    _, tenant, app = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    session = async_to_sync(service.open_session)(token)
    key = tmp_path / "eleven.key"
    key.write_text("test-eleven-secret")
    settings.ELEVENLABS_API_KEY_FILE = str(key)
    enqueue_voice_metrics(tenant, session.session_id, "conv_test")
    job = Job.objects.get(kind="eleven_metrics")
    reads = 0
    posts: list[bytes] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if request.url.host == "api.elevenlabs.io":
            reads += 1
            return httpx.Response(
                200,
                json={
                    "conversation_id": "conv_test",
                    "status": "processing" if reads == 1 else "done",
                    "transcript": [
                        {"message": "PRIVATE CANDIDATE CONTENT", "producing_llm": "native-voice"}
                    ],
                    "metadata": {
                        "start_time_unix_secs": 1793448000,
                        "call_duration_secs": 12,
                        "cost_fiat": 0.00123,
                        "phone_call": {"external_number": "PRIVATE"},
                    },
                },
            )
        if request.method == "GET":
            if request.url.path == "/api/public/v2/observations":
                spans = (
                    json.loads(posts[0])["resourceSpans"][0]["scopeSpans"][0]["spans"]
                    if posts
                    else []
                )
                return httpx.Response(
                    200,
                    json={"data": [{"id": s["spanId"], "traceId": s["traceId"]} for s in spans]},
                )
            return httpx.Response(200, json={"data": [{"id": "test-project"}]})
        posts.append(request.content)
        if len(posts) == 1:
            raise httpx.ReadTimeout("test-eleven-secret")
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    for attempt in range(3):
        job.run_at = timezone.now()
        job.save(update_fields=["run_at"])
        run_once("voice-measurements")
        job.refresh_from_db()
        assert job.status == ("done" if attempt == 2 else "queued")
        assert "PRIVATE" not in str(job.payload)
        assert "test-eleven-secret" not in job.last_error
    assert reads == 2
    assert len(posts) == 1
    assert "PRIVATE" not in posts[0].decode()
    span = json.loads(posts[0])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in span["attributes"]}
    assert attrs["langfuse.observation.model.name"] == "native-voice"
    assert json.loads(attrs["langfuse.observation.cost_details"]) == {"total": 0.00123}
    assert "metric_spans" not in job.payload
    from web.tenancy.context import tenant_context
    from web.tracing.models import VoiceMeasurement

    with tenant_context(tenant):
        assert VoiceMeasurement.objects.count() == 1
    with tenant_context(uuid4()):
        assert VoiceMeasurement.objects.count() == 0


def test_withdrawal_cancels_metrics_and_reconciliation_cannot_export_them(
    export_config: None, phone: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.core.management import call_command

    from screening.platform.container import get_container
    from web.conversations.privacy import redact_candidate
    from web.tenancy.context import tenant_context
    from web.tracing.langfuse import enqueue_voice_metrics
    from web.tracing.models import VoiceMeasurement

    _, tenant_id, app_id = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant_id, app_id)
    session = async_to_sync(service.open_session)(token)
    async_to_sync(DjangoTraceSink().record_llm_call)(
        LlmUsage(
            tenant_id,
            session.session_id,
            None,
            "conversador",
            "test-model",
            "low",
            10,
            2,
            0,
            0,
            1,
            5,
            "stop",
        )
    )
    enqueue_voice_metrics(tenant_id, session.session_id, "conv_withdrawn")
    with tenant_context(tenant_id):
        VoiceMeasurement.objects.create(
            tenant_id=tenant_id,
            session_id=session.session_id,
            conversation_id="conv_withdrawn",
            project_id="test-project",
            spans=[],
        )

    def forbid_network(*args: Any, **kwargs: Any) -> httpx.Response:
        pytest.fail("Withdrawn candidates must not produce remote exports")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    redact_candidate(tenant_id, app_id, "privacy-test", apply=True)
    with tenant_context(tenant_id):
        assert not VoiceMeasurement.objects.exists()
    assert set(
        Job.objects.filter(kind__in=["langfuse_export", "eleven_metrics"]).values_list(
            "status", flat=True
        )
    ) == {"cancelled"}
    call_command("export_metrics", tenant="grupo_sazon", since="2026-01-01T00:00:00Z", apply=True)
    run_once("withdrawal-test")
    # Even a legacy job missing session_id must be stopped by the handler.
    job = Job.objects.get(kind="langfuse_export")
    from web.tracing.models import LlmCall

    with tenant_context(tenant_id):
        row = LlmCall.objects.get(session_id=session.session_id)
    job.payload = {"call_id": str(row.id), "project_id": "test-project"}
    job.status = "queued"
    job.save()
    run_once("withdrawal-test")
    job.refresh_from_db()
    assert job.payload["delivery"] == "suppressed_withdrawn"
