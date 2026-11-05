from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync

from adapters.connectors_registry import DefaultConnectorRegistry
from adapters.fakes.clock import FixedClock
from screening.domain.errors import NotFound
from screening.domain.profile import Connection
from web.simulated.connectors import (
    SimAtsConnector,
    SimCalendarConnector,
    SimSitesConnector,
    normalize,
)
from web.simulated.models import AtsRecord, CalendarSlot, Place, Site
from web.simulated.seed import ensure_slots, load_places, load_sites
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)
NOW = datetime(2026, 10, 22, 8, tzinfo=UTC)
SEEDS = Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon"


@pytest.fixture
def data() -> tuple[Tenant, FixedClock]:
    tenant = Tenant.objects.create(slug="grupo_sazon", name="Grupo Sazón")
    load_sites(tenant, SEEDS / "sites.csv")
    load_places(tenant, SEEDS / "places.csv")
    return tenant, FixedClock(NOW)


def sites_connection() -> Connection:
    return Connection(
        id="sites",
        name="Locales",
        kind="data",
        system="simulado",
        settings={
            "driver": "sim_sites",
            "dataset": "sazon_locales",
            "speed_kmh": "25",
            "travel_mode": "motorcycle",
        },
    )


@pytest.mark.parametrize(
    ("near", "shift", "status", "places", "first", "minutes"),
    [
        ("Benimaclet", "afternoon", "ok", ["Benimaclet (València)"], "VLC-BMC", 1),
        ("Russafa", "any", "ok", ["Ruzafa (València)"], "VLC-RUZ", None),
        ("El Grao", "any", "ambiguous", ["El Grao (Castellón)", "El Grao (València)"], None, None),
        (
            "vivo en el grao",
            "any",
            "ambiguous",
            ["El Grao (Castellón)", "El Grao (València)"],
            None,
            None,
        ),
        ("El Grao, València", "any", "ok", ["El Grao (València)"], "VLC-CAB", None),
        ("el grao de Castellón", "night", "ok", ["El Grao (Castellón)"], "CAS-GRA", None),
        ("Torrent", "morning", "ok", ["Torrent (Torrent)"], "VLC-PAT", 21),
        ("Marte", "any", "not_found", [], None, None),
    ],
)
def test_openings(
    data: tuple[Tenant, FixedClock],
    near: str,
    shift: str,
    status: str,
    places: list[str],
    first: str | None,
    minutes: int | None,
) -> None:
    tenant, clock = data
    result = async_to_sync(SimSitesConnector(tenant.slug, sites_connection(), clock).call)(
        "find_openings", {"near": near, "shift": shift}
    )
    assert result["status"] == status
    assert result["places"] == places
    if first:
        assert result["openings"][0]["site_id"] == first
        assert result["openings"][0]["shift"] == shift
        assert result["openings"][0]["origin"] in places
        assert result["openings"][0]["travel_mode"] == "motorcycle"
        assert result["openings"][0]["estimate"] is True
        if minutes is not None:
            assert result["openings"][0]["minutes"] == minutes
    else:
        assert result["openings"] == []


def test_normalization() -> None:
    assert normalize("  EL  Gráo,  València! ") == "el grao valencia"


def test_forty_five_synthetic_sites_cover_spain_and_mexico(data: tuple[Tenant, FixedClock]) -> None:
    tenant, clock = data
    with tenant_context(tenant.id):
        assert Site.objects.count() == 45
        assert Site.objects.filter(country="MX").count() == 25
        assert Site.objects.filter(country="ES").count() == 20
    connector = SimSitesConnector(tenant.slug, sites_connection(), clock)
    result = async_to_sync(connector.call)("find_openings", {"near": "CDMX", "shift": "any"})
    assert result["status"] == "ok"
    assert all(site["country"] == "MX" for site in result["openings"])
    assert result["openings"][0]["timezone"] == "America/Mexico_City"


def test_live_sites_and_calendar_warnings(data: tuple[Tenant, FixedClock]) -> None:
    tenant, clock = data
    with tenant_context(tenant.id):
        site = Site.objects.get(external_id="VLC-BMC")
        site.has_calendar = False
        site.save(update_fields=["has_calendar"])
    result = async_to_sync(SimSitesConnector(tenant.slug, sites_connection(), clock).call)(
        "status", {"schedule_enabled": True}
    )
    assert any(warning["code"] == "site_without_calendar" for warning in result["warnings"])
    calendar = Connection(
        id="calendar",
        name="Agenda",
        kind="calendar",
        system="sim",
        settings={"driver": "sim_calendar"},
    )
    result = async_to_sync(SimCalendarConnector(tenant.slug, calendar, clock).call)("status", {})
    assert result["warnings"] and all(
        warning["code"] == "calendar_full" for warning in result["warnings"]
    )
    ensure_slots(tenant, NOW)
    result = async_to_sync(SimCalendarConnector(tenant.slug, calendar, clock).call)("status", {})
    assert not result["warnings"] and not result["summary"].startswith("0 ")


def test_slots_and_booking(data: tuple[Tenant, FixedClock]) -> None:
    tenant, clock = data
    created = ensure_slots(tenant, NOW)
    assert created > 0
    assert ensure_slots(tenant, NOW) == 0
    with tenant_context(tenant.id):
        site = Site.objects.get(external_id="VLC-BMC")
        past = CalendarSlot.objects.create(
            tenant=tenant, site=site, starts_at=NOW - timedelta(days=1)
        )
        reserved = CalendarSlot.objects.filter(site=site, starts_at__gt=NOW).earliest("starts_at")
        reserved.booked_application_external_id = "other"
        reserved.save()
    conn = SimCalendarConnector(
        tenant.slug,
        Connection(id="calendar", name="Agenda", kind="calendar", system="simulado"),
        clock,
    )
    result = async_to_sync(conn.call)("list_slots", {"site_id": "VLC-BMC"})
    assert len(result["slots"]) == 3
    assert all(slot["slot_id"] not in {str(past.id), str(reserved.id)} for slot in result["slots"])
    assert result["slots"][0]["starts_at_local"] == "viernes 23/10/2026 11:00"
    args = {"slot_id": result["slots"][0]["slot_id"], "application_external_id": "test"}
    assert async_to_sync(conn.call)("book_slot", args)["status"] == "booked"
    assert async_to_sync(conn.call)("book_slot", args)["status"] == "already_booked"
    second = {**args, "slot_id": result["slots"][1]["slot_id"]}
    assert async_to_sync(conn.call)("book_slot", second)["status"] == "already_booked"
    assert (
        async_to_sync(conn.call)("book_slot", {**args, "application_external_id": "another"})[
            "status"
        ]
        == "taken"
    )
    assert (
        async_to_sync(conn.call)(
            "book_slot", {"slot_id": str(past.id), "application_external_id": "late"}
        )["status"]
        == "expired"
    )
    with tenant_context(tenant.id):
        site.has_calendar = False
        site.save()
    assert async_to_sync(conn.call)("list_slots", {"site_id": "VLC-BMC"}) == {"slots": []}


def test_ats_and_registry(data: tuple[Tenant, FixedClock]) -> None:
    tenant, clock = data
    conn = SimAtsConnector(
        tenant.slug, Connection(id="ats", name="ATS", kind="ats", system="simulado"), clock
    )
    with pytest.raises(NotFound):
        async_to_sync(conn.call)("get_application", {"external_id": "missing"})
    with tenant_context(tenant.id):
        AtsRecord.objects.create(
            tenant=tenant,
            external_id="one",
            candidate_name="Lucía",
            city="València",
            job_ref="repartidores",
            phone="",
            stage="Nuevo",
        )
    assert async_to_sync(conn.call)("get_application", {"external_id": "one"}) == {
        "candidate_name": "Lucía",
        "city": "València",
        "job_ref": "repartidores",
    }
    with pytest.raises(ValueError, match="desconocida"):
        async_to_sync(conn.call)("unknown", {})
    registry = DefaultConnectorRegistry(clock)
    assert isinstance(registry.get(tenant.slug, sites_connection()), SimSitesConnector)
    with pytest.raises(NotFound):
        registry.get(tenant.slug, sites_connection().model_copy(update={"transport": "mcp"}))


def test_seed_counts_are_idempotent(data: tuple[Tenant, FixedClock]) -> None:
    tenant, _ = data
    with tenant_context(tenant.id):
        before = (Site.objects.count(), Place.objects.count())
    load_sites(tenant, SEEDS / "sites.csv")
    load_places(tenant, SEEDS / "places.csv")
    with tenant_context(tenant.id):
        assert (Site.objects.count(), Place.objects.count()) == before
