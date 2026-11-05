import re
from dataclasses import dataclass
from typing import Literal

from screening.domain.capabilities import CATALOG
from screening.domain.profile import ProfileContent, profile_for_offer

BRIEF_LABELS = {
    "role": "El puesto y el cliente",
    "who": "A quién buscamos",
    "fit": "Encaje con el trabajo",
    "voice": "Cómo hablar",
    "followup": "Seguimiento",
    "avoid": "Qué no hacer",
}


@dataclass(frozen=True)
class Check:
    code: str
    severity: Literal["error", "warning", "info"]
    message: str


def run_checks(profile: ProfileContent, live_warnings: list[str] | None = None) -> list[Check]:
    checks: list[Check] = []
    if profile.autonomy.reject > 1:
        checks.append(
            Check("E008", "error", "El descarte necesita revisión humana: usa nivel 0 o 1.")
        )
    for agent in ("conversador", "recruiter"):
        for capability in getattr(profile.grants, agent):
            if agent not in CATALOG[capability].agents:
                checks.append(Check("E009", "error", f"{agent} no puede recibir {capability}."))
    missing_fields = [f.key for f in profile.result_schema if not f.ats_field.strip()]
    if missing_fields:
        checks.append(
            Check(
                "E001",
                "error",
                f"Campos del resultado sin campo en su ATS: {', '.join(missing_fields)}.",
            )
        )
    if not profile.connections:
        checks.append(Check("E002", "error", "No hay ningún sistema conectado."))
    invalid = [
        c.name
        for c in profile.connections
        if c.kind in ("ats", "data", "calendar", "registry")
        and re.fullmatch(r"secret://[a-z0-9_-]+/[a-z0-9_-]+", c.secret_ref or "") is None
    ]
    if invalid:
        checks.append(
            Check(
                "E003",
                "error",
                f"Credenciales que no son una referencia secret:// en: {', '.join(invalid)}.",
            )
        )
    ats = [c for c in profile.connections if c.kind == "ats"]
    if not ats:
        checks.append(
            Check(
                "E004",
                "error",
                "No hay ATS conectado: la plataforma no tendría dónde escribir el resultado.",
            )
        )
    if any(c.permission == "read" for c in ats):
        checks.append(
            Check(
                "E005",
                "error",
                "El ATS está en solo lectura: la plataforma no podrá escribir el resultado.",
            )
        )
    for capability in sorted(set(profile.grants.conversador + profile.grants.recruiter)):
        kinds = CATALOG[capability].needs_kinds
        if kinds and not any(
            c.kind in kinds and capability in c.backs for c in profile.connections
        ):
            checks.append(
                Check("E006", "error", f"{capability}: ningún sistema conectado la respalda.")
            )
    for decision, capability in (
        ("schedule", "interview.book"),
        ("followup", "followup.schedule"),
        ("handoff", "handoff.request"),
    ):
        if (
            getattr(profile.autonomy, decision) >= 1
            and capability not in profile.grants.conversador
        ):
            checks.append(
                Check(
                    "E007",
                    "error",
                    f"El dial permite «{decision}», pero el conversador no tiene {capability}.",
                )
            )
    blank = [
        label
        for key, label in BRIEF_LABELS.items()
        if not (profile.schema_version == 3 and key in ("who", "fit"))
        and not getattr(profile.brief, key).strip()
    ]
    if blank:
        checks.append(Check("W001", "warning", f"Brief sin instrucciones en: {', '.join(blank)}."))
    holes = [
        label for key, label in BRIEF_LABELS.items() if "[Completa" in getattr(profile.brief, key)
    ]
    if holes:
        checks.append(
            Check("W002", "warning", f"Quedan huecos por completar en: {', '.join(holes)}.")
        )
    if not profile.operating_model.strip():
        checks.append(Check("W003", "warning", "El modelo operativo está vacío."))
    if (
        "certificad" in profile.brief.who.lower()
        and "credential.verify" not in profile.grants.conversador
    ):
        checks.append(
            Check(
                "W004",
                "warning",
                "El brief pide comprobar un certificado, "
                "pero el conversador no tiene credential.verify.",
            )
        )
    checks.extend(Check("W005", "warning", text) for text in (live_warnings or []))
    for offer in profile.offers:
        if offer.status == "closed":
            continue
        effective = profile_for_offer(profile, offer.key)
        effective.offers = []
        for check in run_checks(effective):
            if check.severity == "error":
                checks.append(Check(check.code, check.severity, f"{offer.title}: {check.message}"))
        if not offer.description.strip():
            checks.append(
                Check(
                    "E010", "error", f"{offer.title}: falta describir el puesto y sus requisitos."
                )
            )
        if not offer.criteria:
            checks.append(
                Check(
                    "W006",
                    "warning",
                    f"{offer.title}: no tiene criterios de encaje; no se mostrará puntuación.",
                )
            )
    tokens = round(
        sum(len(s.text) for s in profile.sources if s.in_context and s.mode == "context") / 4000
    )
    checks.append(Check("I001", "info", f"Unos {tokens} mil tokens de fuentes en contexto."))
    priority = {"error": 0, "warning": 1, "info": 2}
    return sorted(checks, key=lambda c: (priority[c.severity], c.code))
