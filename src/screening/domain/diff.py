import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from screening.domain.checks import BRIEF_LABELS
from screening.domain.profile import ProfileContent

Risk = Literal["low", "medium", "high"]

AUTONOMY_LABELS = {
    "answer": "Responder dudas",
    "schedule": "Agendar entrevistas",
    "followup": "Reenganchar",
    "handoff": "Derivar a una persona",
    "qualify": "Marcar como apto",
    "reject": "Descartar",
    "brief": "Cambiar su propio perfil",
}


@dataclass(frozen=True)
class Change:
    area: str
    label: str
    before: str
    after: str
    risk: Risk
    needs_client: bool = False
    sentences: list[tuple[str, str]] | None = None


@dataclass(frozen=True)
class ProfileDiff:
    changes: list[Change]
    risk: Literal["none", "low", "medium", "high"]
    needs_second_approval: bool
    needs_client_approval: bool


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?»])\s+", text.strip()) if s.strip()]


def diff_sentences(a: str, b: str) -> list[tuple[str, str]]:
    left, right = split_sentences(a), split_sentences(b)
    lengths = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(len(left) - 1, -1, -1):
        for j in range(len(right) - 1, -1, -1):
            lengths[i][j] = (
                (1 + lengths[i + 1][j + 1])
                if left[i] == right[j]
                else max(lengths[i + 1][j], lengths[i][j + 1])
            )
    result: list[tuple[str, str]] = []
    i = j = 0
    while i < len(left) or j < len(right):
        if i < len(left) and j < len(right) and left[i] == right[j]:
            result.append(("=", left[i]))
            i += 1
            j += 1
        elif i < len(left) and (j == len(right) or lengths[i + 1][j] >= lengths[i][j + 1]):
            result.append(("-", left[i]))
            i += 1
        else:
            result.append(("+", right[j]))
            j += 1
    return result


def _display(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def diff_profiles(
    before: ProfileContent | None, after: ProfileContent, client_approval_required: bool
) -> ProfileDiff:
    changes: list[Change] = []

    def add(
        area: str,
        label: str,
        old: object,
        new: object,
        risk: Risk,
        client: bool = False,
        sentences: bool = False,
    ) -> None:
        if old != new:
            a, b = _display(old), _display(new)
            changes.append(
                Change(area, label, a, b, risk, client, diff_sentences(a, b) if sentences else None)
            )

    if before is None:
        changes = [
            Change("brief", "Primera versión", "", "Primera versión", "medium"),
            Change(
                "result_schema",
                "Contrato del resultado",
                "",
                _display([f.model_dump() for f in after.result_schema]),
                "high",
                True,
            ),
        ]
    else:
        add(
            "configuration",
            "Separación de empresa y ofertas",
            before.schema_version,
            after.schema_version,
            "high",
            True,
        )
        add(
            "offers",
            "Ofertas, requisitos y criterios de encaje",
            [o.model_dump() for o in before.offers],
            [o.model_dump() for o in after.offers],
            "high",
            True,
        )
        for key, label in BRIEF_LABELS.items():
            add(
                "brief",
                label,
                getattr(before.brief, key),
                getattr(after.brief, key),
                "medium",
                sentences=True,
            )
        add(
            "operating_model",
            "Modelo operativo",
            before.operating_model,
            after.operating_model,
            "medium",
            sentences=True,
        )
        old_sources = {s.id: s for s in before.sources}
        new_sources = {s.id: s for s in after.sources}
        for key in sorted(old_sources.keys() | new_sources.keys()):
            old_s, new_s = old_sources.get(key), new_sources.get(key)
            source = new_s or old_s
            assert source is not None
            add("sources", f"Fuente «{source.name}»", old_s, new_s, "low")
        add("channels", "Canales", before.channels, after.channels, "medium")
        add(
            "channels",
            "Configuración de los proveedores de canal",
            before.channel_settings,
            after.channel_settings,
            "high",
            True,
        )
        old_connections = {c.id: c for c in before.connections}
        new_connections = {c.id: c for c in after.connections}
        for key in sorted(old_connections.keys() | new_connections.keys()):
            old_c, new_c = old_connections.get(key), new_connections.get(key)
            if old_c is None and new_c is not None:
                add("connections", f"Conexión «{new_c.name}» añadida", "", new_c, "high", True)
            elif new_c is None and old_c is not None:
                add("connections", f"Conexión «{old_c.name}» quitada", old_c, "", "high", True)
            elif old_c is not None and new_c is not None:
                for field, label, risk in (
                    ("kind", "Tipo", "high"),
                    ("transport", "Transporte", "high"),
                    ("system", "Sistema", "medium"),
                    ("name", "Nombre", "medium"),
                    ("segments", "Segmentos", "high"),
                ):
                    add(
                        "connections",
                        f"{label} de «{new_c.name}»",
                        getattr(old_c, field),
                        getattr(new_c, field),
                        "high" if risk == "high" else "medium",
                        risk == "high",
                    )
                add(
                    "connections",
                    f"Credenciales de «{new_c.name}»",
                    old_c.secret_ref,
                    new_c.secret_ref,
                    "high",
                )
                add(
                    "connections",
                    f"Permiso de «{new_c.name}»",
                    old_c.permission,
                    new_c.permission,
                    "high" if new_c.permission == "write" else "low",
                )
                add(
                    "connections",
                    f"Qué respalda «{new_c.name}»",
                    old_c.backs,
                    new_c.backs,
                    "medium",
                )
                add(
                    "connections",
                    f"Ajustes de «{new_c.name}»",
                    old_c.settings,
                    new_c.settings,
                    "medium",
                )
        for agent in ("conversador", "recruiter"):
            old_grants = set(getattr(before.grants, agent))
            new_grants = set(getattr(after.grants, agent))
            for cap in sorted(new_grants - old_grants):
                add("grants", f"{agent} puede usar {cap}", "", cap, "high")
            for cap in sorted(old_grants - new_grants):
                add("grants", f"{agent} deja de usar {cap}", cap, "", "low")
        old_fields = {f.key: f for f in before.result_schema}
        new_fields = {f.key: f for f in after.result_schema}
        for key in sorted(old_fields.keys() | new_fields.keys()):
            old_f, new_f = old_fields.get(key), new_fields.get(key)
            if old_f is None and new_f is not None:
                add("result_schema", f"Campo «{key}» añadido", "", new_f, "high", True)
            elif new_f is None and old_f is not None:
                add("result_schema", f"Campo «{key}» quitado", old_f, "", "high", True)
            elif old_f is not None and new_f is not None:
                add(
                    "result_schema",
                    f"Validación de «{key}»",
                    old_f.model_dump(exclude={"key", "description", "ats_field", "required"}),
                    new_f.model_dump(exclude={"key", "description", "ats_field", "required"}),
                    "high",
                    True,
                )
                add(
                    "result_schema",
                    f"Campo «{key}» en su ATS",
                    old_f.ats_field,
                    new_f.ats_field,
                    "high",
                    True,
                )
                add(
                    "result_schema",
                    f"Descripción de «{key}»",
                    old_f.description,
                    new_f.description,
                    "medium",
                )
                add(
                    "result_schema",
                    f"«{key}» obligatorio",
                    old_f.required,
                    new_f.required,
                    "high",
                    True,
                )
        for decision, label in AUTONOMY_LABELS.items():
            old_level, new_level = (
                getattr(before.autonomy, decision),
                getattr(after.autonomy, decision),
            )
            add("autonomy", label, old_level, new_level, "high" if new_level > old_level else "low")
        add(
            "rubric",
            "Rúbrica",
            [r.model_dump() for r in before.rubric],
            [r.model_dump() for r in after.rubric],
            "low",
        )
        add(
            "rubric",
            "Umbral de las evals",
            before.eval_threshold,
            after.eval_threshold,
            "high" if after.eval_threshold < before.eval_threshold else "medium",
        )
    if not changes:
        return ProfileDiff([], "none", False, False)
    ranks = {"low": 1, "medium": 2, "high": 3}
    risk = max((c.risk for c in changes), key=lambda r: ranks[r])
    return ProfileDiff(
        changes,
        risk,
        risk == "high",
        client_approval_required and any(c.needs_client for c in changes),
    )
