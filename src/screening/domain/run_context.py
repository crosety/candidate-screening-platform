from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True)
class RunContext:
    agent: Literal[
        "conversador",
        "guardian",
        "evaluador",
        "juez",
        "simulador",
        "copiloto",
        "recruiter",
        "senales",
    ]
    tenant_id: UUID
    tenant_slug: str
    acts_for: str
    talks_to: Literal["candidate", "nobody", "fde", "recruiter"]
    session_id: UUID | None
    application_id: UUID | None
    profile_version_id: UUID | None
    granted: frozenset[str]
    autonomy: dict[str, int]
    eval_case_id: UUID | None = None
    allowed_segments: tuple[str, ...] = ()
    offer_key: str = ""


def run_context_to_dict(ctx: RunContext) -> dict[str, Any]:
    result = asdict(ctx)
    for key in ("tenant_id", "session_id", "application_id", "profile_version_id", "eval_case_id"):
        result[key] = str(result[key]) if result[key] is not None else None
    result["granted"] = sorted(ctx.granted)
    return result


def run_context_from_dict(data: dict[str, Any]) -> RunContext:
    values = dict(data)
    values.setdefault("eval_case_id", None)
    values["allowed_segments"] = tuple(values.get("allowed_segments", ()))
    for key in ("tenant_id", "session_id", "application_id", "profile_version_id", "eval_case_id"):
        values[key] = UUID(values[key]) if values[key] is not None else None
    values["granted"] = frozenset(values["granted"])
    return RunContext(**values)
