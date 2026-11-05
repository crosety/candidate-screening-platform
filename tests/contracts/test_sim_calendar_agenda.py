import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db import connection

from adapters.fakes.clock import FixedClock
from screening.domain.errors import NotAllowed
from screening.domain.profile import Connection
from web.simulated.connectors import SimCalendarConnector
from web.simulated.models import CalendarOperation, CalendarSlot, Site
from web.simulated.seed import ensure_slots, load_sites
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)
NOW = datetime(2026, 10, 22, 8, tzinfo=UTC)
SEEDS = Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon"


@pytest.fixture
def calendar() -> tuple[Tenant, SimCalendarConnector, list[str]]:
    tenant = Tenant.objects.create(slug="grupo_sazon", name="Grupo Sazón")
    load_sites(tenant, SEEDS / "sites.csv")
    ensure_slots(tenant, NOW)
    with tenant_context(tenant.id):
        Site.objects.filter(tenant=tenant, external_id="VLC-RUZ").update(
            address="Calle de Sueca 20"
        )
        slots = [
            str(s.id)
            for s in CalendarSlot.objects.filter(site__external_id="VLC-RUZ").order_by("starts_at")
        ]
    conn = SimCalendarConnector(
        tenant.slug,
        Connection(
            id="agendas",
            name="Agendas",
            kind="calendar",
            system="simulado",
            permission="write",
            settings={"driver": "sim_calendar"},
        ),
        FixedClock(NOW),
    )
    return tenant, conn, slots


def call(conn: SimCalendarConnector, operation: str, **args: Any) -> dict[str, Any]:
    return async_to_sync(conn.call)(operation, args)


def owner(tenant: Tenant, slot_id: str) -> str:
    with tenant_context(tenant.id):
        return CalendarSlot.objects.get(pk=slot_id).booked_application_external_id


def test_book_returns_calendar_facts_and_replays_by_key(
    calendar: tuple[Tenant, SimCalendarConnector, list[str]],
) -> None:
    _, conn, slots = calendar
    booked = call(
        conn, "book_slot", slot_id=slots[0], application_external_id="a", idempotency_key="k1"
    )
    assert booked["status"] == "booked"
    assert booked["site_address"] == "Calle de Sueca 20"
    assert booked["site_timezone"] == "Europe/Madrid" and booked["duration_minutes"] == 15
    assert datetime.fromisoformat(booked["starts_at"]).tzinfo is not None
    again = call(
        conn, "book_slot", slot_id=slots[0], application_external_id="a", idempotency_key="k1"
    )
    assert again == {**booked, "replayed": True}
    with pytest.raises(NotAllowed):
        call(
            conn,
            "cancel_booking",
            slot_id=slots[0],
            application_external_id="a",
            idempotency_key="k1",
        )
    assert len(call(conn, "list_slots", site_id="VLC-RUZ", limit=10)["slots"]) == 10


def test_reschedule_and_cancel_respect_ownership_atomically(
    calendar: tuple[Tenant, SimCalendarConnector, list[str]],
) -> None:
    tenant, conn, slots = calendar
    call(conn, "book_slot", slot_id=slots[0], application_external_id="a")
    call(conn, "book_slot", slot_id=slots[2], application_external_id="b")
    # Nobody can release or move someone else's interview.
    assert (
        call(conn, "cancel_booking", slot_id=slots[0], application_external_id="b")["status"]
        == "not_owner"
    )
    assert (
        call(
            conn,
            "reschedule_booking",
            slot_id=slots[0],
            new_slot_id=slots[1],
            application_external_id="b",
        )["status"]
        == "not_owner"
    )
    assert (
        call(
            conn,
            "reschedule_booking",
            slot_id=slots[0],
            new_slot_id=slots[2],
            application_external_id="a",
        )["status"]
        == "taken"
    )
    assert owner(tenant, slots[0]) == "a" and owner(tenant, slots[2]) == "b"
    moved = call(
        conn,
        "reschedule_booking",
        slot_id=slots[0],
        new_slot_id=slots[1],
        application_external_id="a",
        idempotency_key="move",
    )
    assert moved["status"] == "rescheduled" and moved["previous_slot_id"] == slots[0]
    assert owner(tenant, slots[0]) == "" and owner(tenant, slots[1]) == "a"
    replay = call(
        conn,
        "reschedule_booking",
        slot_id=slots[0],
        new_slot_id=slots[1],
        application_external_id="a",
        idempotency_key="move",
    )
    assert replay["replayed"] is True and owner(tenant, slots[1]) == "a"
    cancelled = call(
        conn, "cancel_booking", slot_id=slots[1], application_external_id="a", idempotency_key="c"
    )
    assert cancelled["status"] == "cancelled" and owner(tenant, slots[1]) == ""
    with tenant_context(tenant.id):
        assert CalendarOperation.objects.count() == 2


def test_concurrent_bookings_of_one_slot_have_one_winner(
    calendar: tuple[Tenant, SimCalendarConnector, list[str]],
) -> None:
    tenant, conn, slots = calendar
    barrier, results = threading.Barrier(2), []

    def book(external_id: str) -> None:
        try:
            barrier.wait()
            results.append(
                call(conn, "book_slot", slot_id=slots[0], application_external_id=external_id)
            )
        finally:
            connection.close()

    threads = [threading.Thread(target=book, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(r["status"] for r in results) == ["booked", "taken"]
    assert owner(tenant, slots[0]) in {"a", "b"}
