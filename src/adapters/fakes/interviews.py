"""In-memory agenda doubles: calendar connector, booking store and chat notifier."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from screening.application.memory import InMemoryInterviewStore as InMemoryInterviewStore
from screening.application.ports import Clock, Connector, SessionRecord
from screening.domain.errors import NotFound
from screening.domain.profile import Connection


@dataclass
class FakeSlot:
    id: str
    site_id: str
    starts_at: datetime
    owner: str = ""
    site_name: str = "Local Centro"
    site_address: str = "Calle Mayor 1"
    timezone: str = "Europe/Madrid"


class InMemoryCalendar:
    """Calendar connector with the same contract as the simulated one, including replays."""

    def __init__(self, clock: Clock, slots: list[FakeSlot], *, delay_s: float = 0.0) -> None:
        self.clock = clock
        self.slots = {slot.id: slot for slot in slots}
        self.ledger: dict[str, tuple[str, dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.delay_s = delay_s
        self._lock = asyncio.Lock()

    def get(self, tenant_slug: str, connection: Connection) -> Connector:
        return self

    def _data(self, slot: FakeSlot) -> dict[str, Any]:
        return {
            "slot_id": slot.id,
            "site_id": slot.site_id,
            "site_name": slot.site_name,
            "site_address": slot.site_address,
            "site_timezone": slot.timezone,
            "starts_at": slot.starts_at.astimezone(UTC).isoformat(),
            "starts_at_local": slot.starts_at.isoformat(),
            "duration_minutes": 15,
        }

    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, dict(args)))
        if operation == "list_slots":
            free = sorted(
                (
                    s
                    for s in self.slots.values()
                    if s.site_id == args["site_id"]
                    and not s.owner
                    and s.starts_at > self.clock.now()
                ),
                key=lambda s: s.starts_at,
            )
            return {"slots": [self._data(s) for s in free[: int(args.get("limit", 3))]]}
        async with self._lock:
            key = str(args.get("idempotency_key") or "")
            if key in self.ledger:
                return {**self.ledger[key][1], "replayed": True}
            result = self._apply(operation, args)
            if key and result["status"] in ("booked", "cancelled", "rescheduled"):
                self.ledger[key] = (operation, result)
        if self.delay_s:
            # The write has happened; the caller only sees a slow (ambiguous) response.
            await asyncio.sleep(self.delay_s)
        return result

    def _apply(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        owner = args["application_external_id"]
        now = self.clock.now()
        if operation == "book_slot":
            if any(s.owner == owner for s in self.slots.values()):
                return {"status": "already_booked"}
            slot = self._slot(args["slot_id"])
            if slot.starts_at <= now:
                return {"status": "expired"}
            if slot.owner:
                return {"status": "taken"}
            slot.owner = owner
            return {"status": "booked", **self._data(slot)}
        if operation == "cancel_booking":
            slot = self._slot(args["slot_id"])
            if slot.owner != owner:
                return {"status": "not_owner"}
            slot.owner = ""
            return {"status": "cancelled", "slot_id": slot.id}
        if operation == "reschedule_booking":
            old, new = self._slot(args["slot_id"]), self._slot(args["new_slot_id"])
            if old.id == new.id:
                return {"status": "same_slot"}
            if old.owner != owner:
                return {"status": "not_owner"}
            if new.starts_at <= now:
                return {"status": "expired"}
            if new.owner:
                return {"status": "taken"}
            old.owner, new.owner = "", owner
            return {"status": "rescheduled", "previous_slot_id": old.id, **self._data(new)}
        raise ValueError(operation)

    def _slot(self, slot_id: str) -> FakeSlot:
        try:
            return self.slots[slot_id]
        except KeyError:
            raise NotFound("Hueco no encontrado") from None


@dataclass
class RecordingNotifier:
    posted: dict[str, tuple[UUID, str]] = field(default_factory=dict)

    async def notify(self, session: SessionRecord, text: str, key: str) -> bool:
        self.posted.setdefault(key, (session.id, text))
        return True
