"""Exports a reproducible eval report: verdict matrix, scores, cost and repetitions."""

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser

from adapters.persistence.evals_gate import run_passes
from screening.domain.profile import ProfileContent
from web.evals.models import EvalCase, EvalRun
from web.profiles.models import ProfileDraft
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def build_report(tenant: Tenant, runs: list[EvalRun], profile: ProfileContent) -> dict[str, Any]:
    matrix: dict[str, dict[str, Any]] = defaultdict(dict)
    rows = []
    for run in runs:
        cases = EvalCase.objects.filter(tenant_id=tenant.id, run=run, target="draft")
        for case in cases.order_by("persona_key"):
            failed = [
                c["text"] for c in case.judge.get("checks", []) if c.get("passed") is not True
            ]
            matrix[case.persona_key][str(run.id)] = {
                "verdict": case.judge.get("verdict", ""),
                "score": case.score,
                "status": case.status,
                "failed_checks": failed,
            }
        rows.append(
            {
                "run_id": str(run.id),
                "created_at": run.created_at.isoformat(),
                "offer_key": run.summary.get("offer_key", ""),
                "status": run.status,
                "score_draft": run.score_draft,
                "cost_usd": round(sum(c.cost_usd_micros for c in cases) / 1_000_000, 6),
                "harness": run.summary.get("harness", {}),
                "gate_passes": run_passes(run, profile),
            }
        )
    scores = [r["score_draft"] for r in rows if r["score_draft"] is not None]
    return {
        "tenant": tenant.slug,
        "runs": rows,
        "repetitions": len(rows),
        "mean_score": round(mean(scores), 2) if scores else None,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "personas": {key: dict(value) for key, value in sorted(matrix.items())},
    }


def markdown(report: dict[str, Any]) -> str:
    runs = [r["run_id"] for r in report["runs"]]
    lines = [
        f"# Informe de evals · {report['tenant']}",
        "",
        f"Repeticiones: {report['repetitions']} · media {report['mean_score']} · "
        f"coste trazado {report['total_cost_usd']} USD (estimación por tokens).",
        "",
        "| Ejecución | Oferta | Estado | Nota | Coste USD | Puerta |",
        "|---|---|---|---|---|---|",
        *(
            f"| {r['run_id'][:8]} | {r['offer_key']} | {r['status']} | {r['score_draft']} | "
            f"{r['cost_usd']} | {'aprobada' if r['gate_passes'] else 'no'} |"
            for r in report["runs"]
        ),
        "",
        "| Persona | " + " | ".join(run[:8] for run in runs) + " |",
        "|---|" + "---|" * len(runs),
    ]
    for persona, cells in report["personas"].items():
        values = []
        for run in runs:
            cell = cells.get(run)
            values.append(
                "—"
                if cell is None
                else f"{cell['verdict']} {cell['score']}" + (" ⚠" if cell["failed_checks"] else "")
            )
        lines.append(f"| {persona} | " + " | ".join(values) + " |")
    lines += ["", "⚠ = alguna comprobación de la persona no se cumplió (ver JSON)."]
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = "Exporta un informe de evals (JSON y Markdown) sin llamar a ningún modelo."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--runs", nargs="+", required=True)
        parser.add_argument("--out", type=Path, required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        tenant = Tenant.objects.get(slug=options["tenant"])
        with tenant_context(tenant.id):
            try:
                ids = [UUID(value) for value in options["runs"]]
            except ValueError as error:
                raise CommandError("Identificador de ejecución no válido") from error
            runs = list(
                EvalRun.objects.filter(tenant_id=tenant.id, id__in=ids).order_by("created_at")
            )
            if len(runs) != len(ids):
                raise CommandError("Alguna ejecución no existe en este cliente")
            draft = ProfileDraft.objects.get(tenant_id=tenant.id)
            report = build_report(tenant, runs, ProfileContent.model_validate(draft.content))
        out: Path = options["out"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "eval-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "eval-report.md").write_text(markdown(report), encoding="utf-8")
        self.stdout.write(f"Informe escrito en {out}")
