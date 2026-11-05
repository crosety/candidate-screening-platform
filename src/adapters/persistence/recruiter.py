from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import CharField, Count, Exists, F, OuterRef, Q, QuerySet, Value
from django.db.models.fields.json import KT, KeyTextTransform, KeyTransform
from django.db.models.functions import Coalesce, NullIf
from django.db.models.lookups import Exact, IContains
from django.utils.timezone import localdate
from pydantic import ValidationError

from screening.application.recruiter import (
    LOCATION_SEMANTICS,
    SHIFT_SEMANTICS,
    AggregateQuery,
    CandidateQuery,
    RecruiterScope,
    phase,
    rate,
    semantic_keys,
    spread,
    stage_label,
)
from screening.domain.profile import FieldSemantic, ProfileContent
from web.conversations.models import Application, Event, Followup, Message, Session
from web.decisions.models import Decision
from web.profiles.models import ProfileVersion
from web.tenancy.context import tenant_context

MOODS = ("neutral", "positive", "confused", "anxious", "frustrated")
PHASES = (("opening", "Inicio"), ("middle", "Desarrollo"), ("closing", "Cierre"))
QUOTES_PER_MOOD = 3
SERIES_DAYS = 366


def scoped(scope: RecruiterScope) -> QuerySet[Session]:
    apps = Application.objects.filter(tenant_id=scope.tenant_id, erased_at__isnull=True)
    if scope.segments != ("*",):
        apps = apps.filter(segment__in=scope.segments)
    return Session.objects.filter(tenant_id=scope.tenant_id, application_id__in=apps.values("id"))


def session_offer() -> Coalesce:
    """Offer frozen in the session's run context; it selects the effective result schema."""
    return Coalesce(KT("run_context__offer_key"), Value(""), output_field=CharField())


def offer_ref() -> Coalesce:
    """Offer for reporting; sessions without a frozen offer fall back to the job ref."""
    return Coalesce(
        NullIf(session_offer(), Value("")), F("application__job_ref"), output_field=CharField()
    )


def in_channel(rows: QuerySet[Session], channel: str) -> QuerySet[Session]:
    # A subquery instead of a join keeps grouped counts at one row per session.
    return rows.filter(Exists(Message.objects.filter(session_id=OuterRef("pk"), channel=channel)))


def aggregate_rows(scope: RecruiterScope, query: AggregateQuery) -> QuerySet[Session]:
    rows = scoped(scope)
    if query.segment:
        rows = rows.filter(application__segment=query.segment)
    if query.channel:
        rows = in_channel(rows, query.channel)
    if query.offer:
        rows = rows.filter(Exact(offer_ref(), query.offer))
    if query.language:
        rows = rows.filter(language="" if query.language == "unknown" else query.language)
    if query.environment:
        rows = rows.filter(application__is_simulation=query.environment == "simulation")
    if query.since:
        rows = rows.filter(created_at__date__gte=query.since)
    if query.until:
        rows = rows.filter(created_at__date__lte=query.until)
    return rows


def load_profiles(tenant_id: UUID, ids: Iterable[UUID]) -> dict[UUID, ProfileContent | None]:
    """Frozen profile versions; an unreadable historical version only loses its labels."""
    profiles: dict[UUID, ProfileContent | None] = {}
    for pk, content in ProfileVersion.objects.filter(
        tenant_id=tenant_id, pk__in=set(ids)
    ).values_list("id", "content"):
        try:
            profiles[pk] = ProfileContent.model_validate(content)
        except ValidationError:
            profiles[pk] = None
    return profiles


def semantic_filter(
    tenant_id: UUID, rows: QuerySet[Session], semantics: tuple[FieldSemantic, ...], text: str
) -> QuerySet[Session]:
    """Match result values by platform meaning, resolving keys per frozen version and offer."""
    combos = set(
        rows.annotate(offer=session_offer()).values_list("profile_version_id", "offer").distinct()
    )
    profiles = load_profiles(tenant_id, (version for version, _ in combos))
    condition = Q(pk__in=[])
    for version_id, offer_key in combos:
        match = Q(pk__in=[])
        for key in semantic_keys(profiles.get(version_id), offer_key, semantics):
            # Explicit transforms: a client key may contain "__" and must stay one JSON key.
            value = KeyTextTransform("value", KeyTransform(key, "result__fields"))
            match |= Q(IContains(value, text))
        condition |= Q(profile_version_id=version_id) & Q(Exact(session_offer(), offer_key)) & match
    return rows.filter(condition)


def counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Status counts and rates over closed conversations; in-progress ones are excluded."""
    status = Counter(row["status"] for row in rows)
    closed = len(rows) - status["active"]
    median_duration, p90_duration = spread(durations(rows))
    return {
        "total": len(rows),
        "in_progress": status["active"],
        "closed": closed,
        "completed": status["completed"],
        "abandoned": status["abandoned"],
        "handed_off": status["handed_off"],
        "completion_pct": rate(status["completed"], closed),
        "abandonment_pct": rate(status["abandoned"], closed),
        "handoff_pct": rate(status["handed_off"], closed),
        "median_duration_seconds": median_duration,
        "p90_duration_seconds": p90_duration,
    }


def durations(rows: list[dict[str, Any]]) -> list[float]:
    return [
        (row["ended_at"] - row["created_at"]).total_seconds()
        for row in rows
        if row["status"] == "completed" and row["ended_at"] is not None
    ]


def breakdown(
    rows: list[dict[str, Any]], keys: dict[UUID, Iterable[str]], labels: dict[str, str]
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for key in keys[row["id"]]:
            groups[key].append(row)
    items = [
        {"key": key, "label": labels.get(key, key), **counts(members)}
        for key, members in groups.items()
    ]
    return sorted(items, key=lambda item: (-item["total"], item["key"]))


def series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Daily activity: starts by start date; completions and abandonments by close date."""
    days: dict[date, Counter[str]] = defaultdict(Counter)
    for row in rows:
        days[localdate(row["created_at"])]["started"] += 1
        if row["status"] == "completed" and row["ended_at"] is not None:
            days[localdate(row["ended_at"])]["completed"] += 1
        if row["status"] == "abandoned":
            days[localdate(row["ended_at"] or row["created_at"])]["abandoned"] += 1
    if not days:
        return []
    last = max(days)
    first = max(min(days), last - timedelta(days=SERIES_DAYS - 1))
    calendar = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
    return [
        {"day": day, **{key: days[day][key] for key in ("started", "completed", "abandoned")}}
        for day in calendar
    ]


def mood_of(signal: Any) -> str:
    mood = signal.get("mood") if isinstance(signal, dict) else None
    return mood if mood in MOODS else "unknown"


def definitions(query: AggregateQuery) -> list[dict[str, str]]:
    population = {
        "": "Incluye operación y simulaciones del FDE.",
        "production": "Solo candidaturas reales; excluye simulaciones.",
        "simulation": "Solo simulaciones del FDE; no es actividad de selección.",
    }[query.environment]
    return [
        {
            "key": "population",
            "text": "Conversaciones iniciadas en el periodo (fecha de inicio) dentro de tu "
            f"alcance y filtros. {population}",
        },
        {
            "key": "completion",
            "text": "Completadas ÷ cerradas. Cerradas = completadas + abandonadas + derivadas; "
            "las conversaciones en curso no cuentan en el denominador.",
        },
        {"key": "abandonment", "text": "Abandonadas ÷ cerradas (mismo denominador)."},
        {"key": "handoff", "text": "Derivadas a una persona ÷ cerradas (mismo denominador)."},
        {
            "key": "duration",
            "text": "Del inicio al cierre, solo conversaciones completadas. Mediana y percentil "
            "90 con interpolación lineal.",
        },
        {
            "key": "first_response",
            "text": "Del primer mensaje del candidato a la primera respuesta posterior del "
            "asistente o del equipo.",
        },
        {
            "key": "funnel",
            "text": "Iniciadas → con al menos un mensaje del candidato → completadas con "
            "respuesta. Porcentaje sobre iniciadas.",
        },
        {
            "key": "stages",
            "text": "Dato que seguía pendiente cuando la conversación se cerró sin respuesta, con "
            "el nombre del campo en la versión de perfil de esa conversación.",
        },
        {
            "key": "channel",
            "text": "Canales con mensajes en la conversación; una conversación que usa varios "
            "canales cuenta en cada uno.",
        },
        {
            "key": "offer",
            "text": "Oferta fijada al iniciar la conversación; título según su versión de perfil.",
        },
        {
            "key": "language",
            "text": "Último idioma observado en los mensajes del candidato.",
        },
        {
            "key": "series",
            "text": "Por día natural (UTC): iniciadas por fecha de inicio; completadas y "
            "abandonadas por fecha de cierre.",
        },
        {
            "key": "mood",
            "text": "Observación del tono de cada mensaje del candidato para adaptar la "
            "conversación. No es un criterio de selección ni interviene en decisiones. Inicio = "
            "primer mensaje; Cierre = último mensaje de una conversación cerrada.",
        },
    ]


def metrics(scope: RecruiterScope, query: AggregateQuery) -> dict[str, Any]:
    tenant_id = scope.tenant_id
    rows = aggregate_rows(scope, query)
    sessions = list(
        rows.annotate(session_offer=session_offer(), offer_ref=offer_ref())
        .order_by("created_at", "id")
        .values(
            "id",
            "status",
            "channel",
            "language",
            "abandoned_stage",
            "created_at",
            "ended_at",
            "profile_version_id",
            "session_offer",
            "offer_ref",
        )
    )
    by_id = {row["id"]: row for row in sessions}
    profiles = load_profiles(tenant_id, {row["profile_version_id"] for row in sessions})
    inbound: dict[UUID, list[tuple[UUID, datetime, Any, str]]] = defaultdict(list)
    channels: dict[UUID, set[str]] = defaultdict(set)
    first_in: dict[UUID, datetime] = {}
    first_response: dict[UUID, float] = {}
    for message_id, sid, direction, channel, at, signal in (
        Message.objects.filter(tenant_id=tenant_id, session_id__in=rows.values("id"))
        .order_by("created_at", "id")
        .values_list("id", "session_id", "direction", "channel", "created_at", "signal")
    ):
        if channel != "human":
            channels[sid].add(channel)
        if direction == "in":
            inbound[sid].append((message_id, at, signal, channel))
        if query.channel and channel != query.channel:
            continue
        if direction == "in":
            first_in.setdefault(sid, at)
        elif sid in first_in:
            first_response.setdefault(sid, (at - first_in[sid]).total_seconds())

    responded = set()
    sent = list(
        Followup.objects.filter(
            tenant_id=tenant_id, status="sent", session_id__in=rows.values("id")
        ).values_list("session_id", "run_at")
    )
    for sid, run_at in sent:
        if any(at > run_at for _, at, _, _ in inbound[sid]):
            responded.add(sid)
    recipients = len({sid for sid, _ in sent})

    # Historical abandonments only recorded their stage in the latest close event.
    legacy = {
        sid: str(payload.get("stage", ""))
        for sid, payload in Event.objects.filter(
            tenant_id=tenant_id,
            type="followups_exhausted",
            session_id__in=[
                row["id"]
                for row in sessions
                if row["status"] == "abandoned" and not row["abandoned_stage"]
            ],
        )
        .order_by("created_at", "id")
        .values_list("session_id", "payload")
    }
    stage_rows: dict[str, dict[str, Any]] = {}
    legacy_stages: Counter[str] = Counter()
    abandoned = [row for row in sessions if row["status"] == "abandoned"]
    for row in abandoned:
        key = row["abandoned_stage"] or legacy.get(row["id"], "")
        legacy_stages[key or "unknown"] += 1
        label, order = stage_label(
            profiles.get(row["profile_version_id"]), row["session_offer"], key
        )
        item = stage_rows.setdefault(key, {"key": key, "label": label, "order": order, "count": 0})
        # Labels follow the most recent session's frozen version; order keeps the earliest.
        item.update(label=label, order=min(order, item["order"]), count=item["count"] + 1)
        item.setdefault("moods", Counter())[
            mood_of(inbound[row["id"]][-1][2]) if inbound[row["id"]] else "unknown"
        ] += 1
    stages = [
        {
            **{k: v for k, v in item.items() if k != "moods"},
            "pct": rate(item["count"], len(abandoned)),
            "moods": [
                {"label": mood, "count": item["moods"][mood]}
                for mood in (*MOODS, "unknown")
                if item["moods"][mood]
            ],
        }
        for item in sorted(stage_rows.values(), key=lambda item: (item["order"], item["label"]))
    ]

    mood_counts: dict[str, Counter[str]] = defaultdict(Counter)
    quotes: dict[tuple[str, str], list[tuple[datetime, UUID, UUID, str]]] = defaultdict(list)
    for sid, messages in inbound.items():
        observed = [m for m in messages if not query.channel or m[3] == query.channel]
        closed = by_id[sid]["status"] != "active"
        for position, (message_id, at, signal, _) in enumerate(observed):
            key, mood = phase(position, len(observed), closed), mood_of(signal)
            mood_counts[key][mood] += 1
            quote = signal.get("quote") if isinstance(signal, dict) else None
            if scope.conversations and mood not in ("neutral", "unknown") and quote:
                quotes[(key, mood)].append((at, sid, message_id, str(quote)))
    shown = {
        bucket: sorted(items, key=lambda item: item[0], reverse=True)[:QUOTES_PER_MOOD]
        for bucket, items in quotes.items()
    }
    # Defense in depth: only a literal fragment of the candidate's own message is shown.
    texts = dict(
        Message.objects.filter(
            tenant_id=tenant_id, pk__in=[item[2] for items in shown.values() for item in items]
        ).values_list("id", "text")
    )
    moods = [
        {
            "key": key,
            "label": label,
            "total": sum(mood_counts[key].values()),
            "items": [
                {
                    "mood": mood,
                    "count": mood_counts[key][mood],
                    "quotes": [
                        {"session_id": sid, "message_id": mid, "quote": quote, "created_at": at}
                        for at, sid, mid, quote in shown.get((key, mood), [])
                        if quote in texts.get(mid, "")
                    ],
                }
                for mood in (*MOODS, "unknown")
            ],
        }
        for key, label in PHASES
    ]

    replied = {sid for sid in by_id if inbound[sid]}
    total = len(sessions)
    funnel = [
        ("started", "Conversaciones iniciadas", total),
        ("replied", "Con respuesta del candidato", len(replied)),
        (
            "completed",
            "Completadas",
            sum(1 for sid in replied if by_id[sid]["status"] == "completed"),
        ),
    ]
    titles: dict[str, str] = {"": "Sin oferta"}
    for row in sessions:
        profile = profiles.get(row["profile_version_id"])
        for offer in profile.offers if profile else []:
            titles[offer.key] = offer.title
    duration = durations(sessions)
    median_first, p90_first = spread(list(first_response.values()))
    return {
        "group": "metrics",
        **counts(sessions),
        "mean_duration_seconds": mean(duration) if duration else None,
        "duration_samples": len(duration),
        "mean_first_response_seconds": mean(first_response.values()) if first_response else None,
        "median_first_response_seconds": median_first,
        "p90_first_response_seconds": p90_first,
        "first_response_samples": len(first_response),
        "abandonment_stages": [
            {"label": label, "count": n} for label, n in sorted(legacy_stages.items())
        ],
        "followup_recipients": recipients,
        "responded_after_followup": len(responded),
        "response_pct": rate(len(responded), recipients),
        "note": "Respuesta posterior al envío; asociación descriptiva, no causalidad.",
        "funnel": [
            {"key": key, "label": label, "count": n, "pct": rate(n, total)}
            for key, label, n in funnel
        ],
        "stages": stages,
        "by_channel": breakdown(
            sessions, {row["id"]: channels[row["id"]] or {row["channel"]} for row in sessions}, {}
        ),
        "by_offer": breakdown(
            sessions, {row["id"]: [row["offer_ref"]] for row in sessions}, titles
        ),
        "by_language": breakdown(
            sessions, {row["id"]: [row["language"] or "unknown"] for row in sessions}, {}
        ),
        "series": series(sessions),
        "moods": moods,
        "quotes_visible": scope.conversations,
        "definitions": definitions(query),
    }


class DjangoRecruiterReader:
    @sync_to_async
    def query(self, scope: RecruiterScope, query: CandidateQuery) -> dict[str, Any]:
        with tenant_context(scope.tenant_id):
            rows = scoped(scope)
            if query.status:
                rows = rows.filter(status=query.status)
            if query.segment:
                rows = rows.filter(application__segment=query.segment)
            if query.verdict:
                rows = rows.filter(result__verdict=query.verdict)
            if query.location:
                rows = semantic_filter(scope.tenant_id, rows, LOCATION_SEMANTICS, query.location)
            if query.shift:
                rows = semantic_filter(scope.tenant_id, rows, SHIFT_SEMANTICS, query.shift)
            if query.channel:
                rows = in_channel(rows, query.channel)
            if query.since:
                rows = rows.filter(created_at__date__gte=query.since)
            if query.until:
                rows = rows.filter(created_at__date__lte=query.until)
            count = rows.count()
            return {
                "total": count,
                "items": list(
                    rows.order_by("-created_at", "id").values(
                        "id",
                        "application__candidate_name",
                        "application__segment",
                        "status",
                        "profile_version__version",
                        "created_at",
                    )[query.offset : query.offset + query.limit]
                ),
            }

    @sync_to_async
    def aggregates(self, scope: RecruiterScope, query: AggregateQuery) -> dict[str, Any]:
        if query.group == "metrics":
            with tenant_context(scope.tenant_id):
                return metrics(scope, query)
        if query.group == "agreement":
            with tenant_context(scope.tenant_id):
                decisions = Decision.objects.filter(
                    tenant_id=scope.tenant_id,
                    session_id__in=aggregate_rows(scope, query).values("id"),
                    decided_by="agent",
                    status__in=["applied", "overridden"],
                ).exclude(user=None)
                items = []
                for version in decisions.values_list(
                    "session__profile_version__version", flat=True
                ).distinct():
                    subset = decisions.filter(session__profile_version__version=version)
                    n = subset.count()
                    agree = subset.filter(status="applied").count()
                    items.append(
                        {
                            "version": version,
                            "reviewed": n,
                            "agree": agree,
                            "agreement_pct": rate(agree, n),
                        }
                    )
                return {
                    "group": "agreement",
                    "items": items,
                    "note": "Solo revisión humana; excluye decisiones automáticas sin revisión.",
                }
        if query.group == "sentiment":
            with tenant_context(scope.tenant_id):
                observed: Counter[str] = Counter()
                for values in aggregate_rows(scope, query).values_list(
                    "result__sentiment", flat=True
                ):
                    for item in values or []:
                        observed[f"{item['stage']} · {item['label']}"] += 1
                return {
                    "group": "sentiment",
                    "unit": "observación por etapa, no decisión",
                    "items": [{"label": label, "count": n} for label, n in observed.items()],
                }
        field = {
            "status": "status",
            "segment": "application__segment",
            "version": "profile_version__version",
            "verdict": "result__verdict",
            "channel": "channel",
            "offer": "offer_ref",
            "language": "language",
        }[query.group]
        with tenant_context(scope.tenant_id):
            return {
                "group": query.group,
                "items": [
                    {"label": row[field] or "Sin dato", "count": row["count"]}
                    for row in aggregate_rows(scope, query)
                    .annotate(offer_ref=offer_ref())
                    .values(field)
                    .annotate(count=Count("id"))
                    .order_by(field)
                ],
            }
