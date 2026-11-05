import json
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest
from django.core.management import call_command
from django.test import Client

from tests.api.test_conversations_api import conversation_client as conversation_client
from web.evals.models import EvalRun
from web.jobs.models import Job
from web.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db(transaction=True)


def test_fake_command_executes_without_enqueuing(conversation_client: tuple[Client, UUID]) -> None:
    _, tenant = conversation_client
    output = StringIO()
    call_command(
        "run_evals",
        tenant="grupo_sazon",
        offer="repartidores",
        personas="lucia_carne_a2",
        max_cost=1,
        no_compare=True,
        stdout=output,
    )
    assert "review" in output.getvalue() and "40.0" in output.getvalue()
    assert not Job.objects.exists()
    with tenant_context(tenant):
        assert EvalRun.objects.get().status == "done"


def test_report_exports_matrix_cost_and_gate_without_models(
    conversation_client: tuple[Client, UUID], tmp_path: Path
) -> None:
    _, tenant = conversation_client
    for _ in range(2):
        call_command(
            "run_evals",
            tenant="grupo_sazon",
            offer="repartidores",
            personas="lucia_carne_a2",
            max_cost=1,
            no_compare=True,
            stdout=StringIO(),
        )
    with tenant_context(tenant):
        runs = [str(run.id) for run in EvalRun.objects.order_by("created_at")]
    call_command(
        "export_eval_report", tenant="grupo_sazon", runs=runs, out=tmp_path, stdout=StringIO()
    )
    report = json.loads((tmp_path / "eval-report.json").read_text())
    assert report["repetitions"] == 2
    assert list(report["personas"]) == ["lucia_carne_a2"]
    assert len(report["personas"]["lucia_carne_a2"]) == 2
    # Fake mode never opens the publication gate.
    assert all(run["gate_passes"] is False for run in report["runs"])
    markdown = (tmp_path / "eval-report.md").read_text()
    assert "| lucia_carne_a2 |" in markdown and "Puerta" in markdown
