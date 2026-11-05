"""Recruiter analytics: metric definitions, semantic filters, labels, moods and scope."""

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.test import Client

from adapters.persistence.recruiter import DjangoRecruiterReader
from screening.application.recruiter import AggregateQuery, RecruiterScope, phase, spread
from screening.domain.profile import ProfileContent
from screening.platform.container import get_container
from web.accounts.models import User
from web.conversations.models import Application, Event, Message, Session
from web.decisions.models import Result
from web.profiles.models import ProfileVersion
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership, Tenant

pytestmark = pytest.mark.django_db(transaction=True)

START = datetime(2026, 10, 20, 9, tzinfo=UTC)
serial = count()


@pytest.fixture
def world(client: Client) -> dict[str, Any]:
    """Demo tenant plus a frozen version whose site field is not called local_asignado."""
    call_command("seed_local")
    tenant = Tenant.objects.get(slug="grupo_sazon")
    fde = User.objects.get(email="fde@orbio.local")
    active = get_container().profiles.active_version(tenant.id)
    assert active is not None
    content = active.content.model_dump(mode="json")
    content["result_schema"] = [
        {"key": "permiso", "description": "Permiso vigente", "label": "Permiso de conducir"},
        {
            "key": "centro",
            "description": "Centro",
            "label": "Centro de trabajo",
            "semantic": "site",
        },
        {"key": "franja", "description": "Franja", "label": "Franja horaria", "semantic": "shift"},
        {"key": "sin_label", "description": "Campo sin etiqueta"},
    ]
    content["offers"] = [
        {
            "key": "motos",
            "title": "Reparto en moto",
            "status": "open",
            "fields": [
                {
                    "key": "base",
                    "description": "Base",
                    "label": "Base asignada",
                    "semantic": "site",
                },
                {"key": "franja", "description": "Franja", "label": "Franja", "semantic": "shift"},
            ],
        }
    ]
    ProfileContent.model_validate(content)
    with tenant_context(tenant.id):
        seq = (
            ProfileVersion.objects.filter(tenant=tenant)
            .order_by("-seq")
            .values_list("seq", flat=True)[0]
        )
        custom = ProfileVersion.objects.create(
            tenant=tenant,
            seq=seq + 1,
            version="9.0",
            content=content,
            content_hash="0" * 64,
            schema_version=2,
            risk="low",
            published_by=fde,
        )
    client.force_login(fde)
    return {"client": client, "tenant": tenant, "seed": active.id, "custom": custom.id}


def make(
    world: dict[str, Any],
    *,
    status: str = "active",
    minutes: float | None = None,
    start: datetime = START,
    version: str = "custom",
    offer_key: str | None = None,
    job_ref: str = "repartidores",
    segment: str = "",
    language: str = "",
    stage: str = "",
    simulation: bool = False,
    messages: tuple[tuple[str, str, float, str, dict[str, Any]], ...] = (),
    fields: dict[str, Any] | None = None,
) -> Session:
    """Session with explicit times; each message is (direction, text, seconds, channel, signal)."""
    tenant = world["tenant"]
    n = next(serial)
    with tenant_context(tenant.id):
        app = Application.objects.create(
            tenant=tenant,
            external_id=f"analytics-{n}",
            candidate_name=f"Candidata {n}",
            phone="",
            city="València",
            job_ref=job_ref,
            segment=segment,
            is_simulation=simulation,
            is_test=simulation,
        )
        session = Session.objects.create(
            tenant=tenant,
            application=app,
            profile_version_id=world[version],
            channel="web",
            status=status,
            run_context={"offer_key": offer_key} if offer_key is not None else {},
            system_prompt="",
            language=language,
            abandoned_stage=stage,
        )
        ended = start + timedelta(minutes=minutes) if minutes is not None else None
        Session.objects.filter(pk=session.id).update(created_at=start, ended_at=ended)
        for direction, text, seconds, channel, signal in messages:
            message = Message.objects.create(
                tenant=tenant,
                session=session,
                direction=direction,
                text=text,
                channel=channel,
                language=language if direction == "in" else "",
                signal=signal,
            )
            Message.objects.filter(pk=message.id).update(
                created_at=start + timedelta(seconds=seconds)
            )
        if fields is not None:
            Result.objects.create(
                tenant=tenant,
                session=session,
                verdict="review",
                summary="",
                evaluator_model="fake",
                fields=fields,
            )
        session.refresh_from_db()
        return session


def metrics(world: dict[str, Any], query: str = "") -> dict[str, Any]:
    response = world["client"].get(
        f"/api/v1/tenants/{world['tenant'].id}/analytics?group=metrics{query}"
    )
    assert response.status_code == 200, response.content
    data: dict[str, Any] = response.json()
    return data


def test_percentiles_and_phases_are_explicit() -> None:
    assert spread([]) == (None, None)
    assert spread([42]) == (42, 42)
    assert spread([60.0 * m for m in range(1, 11)]) == (330.0, 546.0)
    assert [phase(i, 3, closed=True) for i in range(3)] == ["opening", "middle", "closing"]
    assert [phase(i, 2, closed=False) for i in range(2)] == ["opening", "middle"]
    assert phase(0, 1, closed=True) == "opening"


def test_rates_exclude_conversations_in_progress(world: dict[str, Any]) -> None:
    make(world, status="completed", minutes=10)
    make(world, status="completed", minutes=20)
    make(world, status="abandoned", minutes=60 * 24)
    make(world, status="handed_off")
    make(world, status="active")
    make(world, status="active")
    data = metrics(world)
    assert (data["total"], data["in_progress"], data["closed"]) == (6, 2, 4)
    assert (data["completed"], data["abandoned"], data["handed_off"]) == (2, 1, 1)
    assert data["completion_pct"] == 50.0
    assert data["abandonment_pct"] == 25.0
    assert data["handoff_pct"] == 25.0
    # Durations only cover completed conversations; the 24 h abandonment is excluded.
    assert data["duration_samples"] == 2
    assert data["mean_duration_seconds"] == 900
    assert (data["median_duration_seconds"], data["p90_duration_seconds"]) == (900, 1140)
    texts = {item["key"]: item["text"] for item in data["definitions"]}
    assert "en curso no cuentan en el denominador" in texts["completion"]
    assert "solo conversaciones completadas" in texts["duration"]


def test_duration_and_first_response_median_and_p90(world: dict[str, Any]) -> None:
    for minute in range(1, 11):
        make(
            world,
            status="completed",
            minutes=minute,
            messages=(
                ("out", "Hola, soy el asistente", 0, "web", {}),
                ("in", "Hola", 10, "web", {}),
                ("out", "Gracias", 10 + minute, "web", {}),
            ),
        )
    data = metrics(world)
    assert (data["median_duration_seconds"], data["p90_duration_seconds"]) == (330, 546)
    # The assistant answered `minute` seconds after the candidate's first message.
    assert data["first_response_samples"] == 10
    assert data["median_first_response_seconds"] == 5.5
    assert data["p90_first_response_seconds"] == 9.1
    assert data["mean_first_response_seconds"] == 5.5
    assert data["funnel"] == [
        {"key": "started", "label": "Conversaciones iniciadas", "count": 10, "pct": 100.0},
        {"key": "replied", "label": "Con respuesta del candidato", "count": 10, "pct": 100.0},
        {"key": "completed", "label": "Completadas", "count": 10, "pct": 100.0},
    ]


def test_breakdowns_by_channel_offer_and_language(world: dict[str, Any]) -> None:
    make(
        world,
        status="completed",
        minutes=5,
        offer_key="motos",
        language="es",
        messages=(("in", "Hola", 1, "web", {}), ("in", "Sigo por aquí", 2, "whatsapp", {})),
    )
    make(
        world,
        status="abandoned",
        minutes=30,
        version="seed",
        language="en",
        messages=(("in", "Hi", 1, "whatsapp", {}), ("out", "Hola", 2, "human", {})),
    )
    make(world, status="active", version="seed", job_ref="sin-perfil")
    make(world, status="completed", minutes=5, simulation=True, language="es")
    data = metrics(world, "&environment=production")
    assert data["total"] == 3
    channels = {row["key"]: row for row in data["by_channel"]}
    # Several channels count in each; team replies are not a candidate channel.
    assert set(channels) == {"web", "whatsapp"}
    assert (channels["web"]["total"], channels["whatsapp"]["total"]) == (2, 2)
    assert channels["whatsapp"]["completion_pct"] == 50.0
    offers = {row["key"]: row for row in data["by_offer"]}
    assert offers["motos"]["label"] == "Reparto en moto"
    assert offers["repartidores"]["label"] == "Repartidor/a · España y México"
    assert offers["sin-perfil"]["label"] == "sin-perfil"
    assert offers["repartidores"]["abandonment_pct"] == 100.0
    languages = {row["key"]: row["total"] for row in data["by_language"]}
    assert languages == {"es": 1, "en": 1, "unknown": 1}
    assert metrics(world)["total"] == 4
    assert metrics(world, "&environment=simulation")["total"] == 1
    assert metrics(world, "&environment=production&offer=motos")["total"] == 1
    assert metrics(world, "&environment=production&offer=repartidores")["total"] == 1
    assert metrics(world, "&environment=production&language=unknown")["total"] == 1
    assert metrics(world, "&channel=whatsapp")["total"] == 2
    assert metrics(world, "&channel=telephone")["total"] == 0  # a valid channel, unused here
    base = f"/api/v1/tenants/{world['tenant'].id}/analytics"
    grouped = world["client"].get(base + "?group=offer&environment=production").json()
    assert {i["label"]: i["count"] for i in grouped["items"]} == {
        "motos": 1,
        "repartidores": 1,
        "sin-perfil": 1,
    }
    assert world["client"].get(base + "?group=language&language=EN").status_code == 422


def test_daily_series_counts_starts_and_closes_on_their_own_day(world: dict[str, Any]) -> None:
    make(world, status="completed", minutes=60 * 24)
    make(world, status="abandoned", minutes=60 * 48, start=START + timedelta(days=1))
    make(world, status="active", start=START + timedelta(days=3))
    series = metrics(world)["series"]
    assert series == [
        {"day": "2026-10-20", "started": 1, "completed": 0, "abandoned": 0},
        {"day": "2026-10-21", "started": 1, "completed": 1, "abandoned": 0},
        {"day": "2026-10-22", "started": 0, "completed": 0, "abandoned": 0},
        {"day": "2026-10-23", "started": 1, "completed": 0, "abandoned": 1},
    ]


def test_location_and_shift_filters_follow_field_semantics(world: dict[str, Any]) -> None:
    shared = make(world, fields={"centro": {"value": "Ruzafa"}, "franja": {"value": "Tarde"}})
    offer = make(world, offer_key="motos", fields={"base": {"value": "ruzafa centro"}})
    # Same key outside its offer schema, and a client key with no platform meaning here.
    make(world, offer_key="motos", fields={"centro": {"value": "Ruzafa"}})
    make(world, fields={"local_asignado": {"value": "Ruzafa"}, "turno": {"value": "tarde"}})
    legacy = make(world, version="seed", fields={"local_asignado": {"value": "Ruzafa"}})
    base = f"/api/v1/tenants/{world['tenant'].id}/candidates"
    found = world["client"].get(base + "?location=Ruzafa").json()
    assert {item["id"] for item in found["items"]} == {
        str(shared.id),
        str(offer.id),
        str(legacy.id),
    }
    shift = world["client"].get(base + "?shift=tarde").json()
    assert [item["id"] for item in shift["items"]] == [str(shared.id)]
    both = world["client"].get(base + "?location=ruzafa&shift=TARDE").json()
    assert both["total"] == 1
    assert world["client"].get(base + "?location=Castellón").json()["total"] == 0


def test_abandonment_stages_use_frozen_labels_with_legacy_fallback(
    world: dict[str, Any],
) -> None:
    frustrated = {"mood": "frustrated", "quote": "otra vez", "tone": "empathize"}
    make(
        world,
        status="abandoned",
        stage="franja",
        messages=(("in", "Ya lo dije otra vez", 1, "web", frustrated),),
    )
    make(world, status="abandoned", stage="franja")
    make(world, status="abandoned", stage="base", offer_key="motos")
    make(world, status="abandoned", stage="sin_label")
    make(world, status="abandoned", stage="interview")
    legacy = make(world, status="abandoned")
    unknown = make(world, status="abandoned")
    stale = make(world, status="abandoned", stage="summary")
    # Events are append-only: created in order, so the latest close event wins.
    with tenant_context(world["tenant"].id):
        for session, stage in ((legacy, "old"), (legacy, "awaiting_candidate"), (stale, "x")):
            Event.objects.create(
                tenant=world["tenant"],
                session=session,
                type="followups_exhausted",
                payload={"stage": stage},
            )
    data = metrics(world)
    stages = [(row["key"], row["label"], row["count"]) for row in data["stages"]]
    # Ordered by position in each session's frozen schema, then non-field steps.
    assert stages == [
        ("base", "Base asignada", 1),
        ("franja", "Franja horaria", 2),
        ("sin_label", "sin_label", 1),
        ("interview", "Cita de entrevista", 1),
        ("summary", "Resumen final", 1),
        ("awaiting_candidate", "Esperando respuesta del candidato", 1),
        ("", "Sin etapa registrada", 1),
    ]
    first = data["stages"][1]
    assert first["pct"] == 25.0
    assert first["moods"] == [
        {"label": "frustrated", "count": 1},
        {"label": "unknown", "count": 1},
    ]
    # The legacy list keeps raw keys for existing consumers.
    legacy_rows = {row["label"]: row["count"] for row in data["abandonment_stages"]}
    assert legacy_rows["awaiting_candidate"] == 1 and legacy_rows["unknown"] == 1
    assert unknown.abandoned_stage == ""


def test_moods_by_phase_with_quotes_only_for_conversation_readers(
    world: dict[str, Any],
) -> None:
    confused = {"mood": "confused", "quote": "no entiendo", "tone": "simplify"}
    session = make(
        world,
        status="completed",
        minutes=10,
        messages=(
            ("out", "Hola", 0, "web", {}),
            ("in", "Hola, no entiendo qué es la franja", 5, "web", confused),
            ("in", "Vale", 10, "web", {"mood": "neutral", "quote": "", "tone": ""}),
            ("in", "Gracias", 15, "web", {"mood": "positive", "quote": "inventado", "tone": ""}),
        ),
    )
    make(world, messages=(("in", "Hola", 1, "web", {}), ("in", "Sigo", 2, "web", {})))
    data = metrics(world)
    phases = {row["key"]: row for row in data["moods"]}
    assert [row["label"] for row in data["moods"]] == ["Inicio", "Desarrollo", "Cierre"]
    counts = {
        key: {item["mood"]: item["count"] for item in row["items"] if item["count"]}
        for key, row in phases.items()
    }
    assert counts == {
        "opening": {"confused": 1, "unknown": 1},
        "middle": {"neutral": 1, "unknown": 1},
        "closing": {"positive": 1},
    }
    assert data["quotes_visible"] is True
    opening = {item["mood"]: item for item in phases["opening"]["items"]}
    assert [q["quote"] for q in opening["confused"]["quotes"]] == ["no entiendo"]
    assert opening["confused"]["quotes"][0]["session_id"] == str(session.id)
    # A quote that is not literal candidate text is never shown.
    closing = {item["mood"]: item for item in phases["closing"]["items"]}
    assert closing["positive"]["quotes"] == []
    fde = User.objects.get(email="fde@orbio.local")
    tenant_id = world["tenant"].id
    assistant = async_to_sync(DjangoRecruiterReader().aggregates)(
        RecruiterScope(tenant_id, fde.id, ("*",)), AggregateQuery(group="metrics")
    )
    assert assistant["quotes_visible"] is False
    assert all(not item["quotes"] for row in assistant["moods"] for item in row["items"])
    detail = world["client"].get(f"/api/v1/tenants/{tenant_id}/candidates/{session.id}").json()
    observed = [(m["direction"], m["language"], m["signal"]) for m in detail["messages"]]
    assert observed[0] == ("out", "", None)
    assert observed[1][2]["mood"] == "confused" and observed[1][2]["quote"] == "no entiendo"
    assert all(m["id"] for m in detail["messages"])


def test_segment_scope_limits_every_new_metric(world: dict[str, Any]) -> None:
    worried = {"mood": "anxious", "quote": "me preocupa", "tone": "reassure"}
    visible = make(
        world,
        status="abandoned",
        segment="north",
        language="es",
        stage="franja",
        messages=(("in", "Hola, me preocupa el horario", 1, "web", worried),),
    )
    hidden = make(
        world,
        status="completed",
        minutes=5,
        segment="south",
        language="en",
        offer_key="motos",
        messages=(("in", "I worry", 1, "whatsapp", {**worried, "quote": "I worry"}),),
    )
    other = Tenant.objects.get(slug="logistica")
    other_version = get_container().profiles.active_version(other.id)
    assert other_version is not None
    with tenant_context(other.id):
        app = Application.objects.create(
            tenant=other, external_id="other-analytics", candidate_name="Otra", job_ref="x"
        )
        Session.objects.create(
            tenant=other,
            application=app,
            profile_version_id=other_version.id,
            channel="web",
            status="completed",
            system_prompt="",
        )
    user = User.objects.get(email="reclutadora@orbio.local")
    Membership.objects.filter(user=user, tenant_id=world["tenant"].id).update(
        allowed_segments=["north"]
    )
    world["client"].force_login(user)
    data = metrics(world)
    assert data["total"] == 1 and data["abandonment_pct"] == 100.0
    assert [row["key"] for row in data["by_language"]] == ["es"]
    assert [row["key"] for row in data["by_channel"]] == ["web"]
    assert [row["key"] for row in data["by_offer"]] == ["repartidores"]
    assert [row["key"] for row in data["stages"]] == ["franja"]
    quotes = [q for row in data["moods"] for item in row["items"] for q in item["quotes"]]
    assert [(q["session_id"], q["quote"]) for q in quotes] == [(str(visible.id), "me preocupa")]
    assert metrics(world, "&segment=south")["total"] == 0
    assert metrics(world, "&offer=motos")["total"] == 0
    base = f"/api/v1/tenants/{world['tenant'].id}"
    assert world["client"].get(f"{base}/candidates/{hidden.id}").status_code == 404
    assert world["client"].get(f"{base}/analytics?group=language").json()["items"] == [
        {"label": "es", "count": 1}
    ]
    assert world["client"].get(f"/api/v1/tenants/{other.id}/analytics").status_code == 404
    Membership.objects.filter(user=user, tenant_id=world["tenant"].id).update(allowed_segments=[])
    assert metrics(world)["total"] == 0
